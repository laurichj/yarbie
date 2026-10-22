"""Reading the map and applying edits, with the safety checks around every write."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any

from . import diff, heatmap, mapdoc
from .sources.base import MapSource, ReadOnlyError
from .sources.robot import RobotSource
from .store import BackupStore


class ConflictError(RuntimeError):
    """The robot's map or state does not allow this write right now."""


@dataclass
class MapView:
    raw: dict[str, Any]
    reference: mapdoc.Ref | None
    revision: str

    def public(self) -> dict[str, Any]:
        ref = mapdoc.site_reference(self.raw, self.reference)
        return {
            "revision": self.revision,
            "reference": list(ref) if ref else None,
            "features": mapdoc.to_feature_collection(self.raw, self.reference),
        }


class MapService:
    def __init__(self, source: MapSource, backups: BackupStore) -> None:
        self.source = source
        self.backups = backups
        self._lock = asyncio.Lock()  # one write at a time
        self.last_reference: mapdoc.Ref | None = None
        self._history: list[dict[str, Any]] | None = None

    @property
    def robot(self) -> RobotSource:
        if not isinstance(self.source, RobotSource):
            raise ReadOnlyError(f"the {self.source.name} source cannot change the robot")
        return self.source

    async def load(self) -> MapView:
        raw = await self.source.raw_map()
        reference = await self.source.reference()
        self.last_reference = mapdoc.site_reference(raw, reference)
        return MapView(raw, reference, mapdoc.map_revision(raw))

    async def heatmap(self, days: int | None, refresh: bool) -> dict[str, Any]:
        """Estimated wear from the robot's run history (read once, then on request)."""
        if refresh or self._history is None:
            self._history = await self.robot.plan_history()
        view = await self.load()
        return await asyncio.to_thread(
            heatmap.estimate, view.raw, self._history, view.reference, since=heatmap.window(days)
        )

    async def preview(self, edited: dict[str, Any]) -> list[diff.Op]:
        view = await self.load()
        return diff.compute(view.raw, view.reference, edited)

    async def apply(self, edited: dict[str, Any], revision: str) -> dict[str, Any]:
        robot = self.robot
        async with self._lock:
            view = await self.load()
            if view.revision != revision:
                raise ConflictError(
                    "The map on the robot changed since you loaded it (perhaps in the app). "
                    "Reload, then make your edits again."
                )
            status = await robot.status()
            if status.busy:
                raise ConflictError("The robot is running or paused in a plan. Stop it first.")
            ops = diff.compute(view.raw, view.reference, edited)
            todo = [op for op in ops if op.supported]
            if not todo:
                return {"applied": [], "skipped": [o.public() for o in ops], "backup": None}
            backup = self.backups.save(view.raw, "before-apply")
            applied: list[dict[str, Any]] = []
            error: str | None = None
            for op in todo:
                try:
                    await self._send(robot, op)
                except Exception as err:  # stop at the first refusal; report what was done
                    error = f"{op.kind} {op.family} {op.name!r}: {err}"
                    break
                applied.append(op.public())
            after = await self.load()
            return {
                "applied": applied,
                "skipped": [o.public() for o in ops if not o.supported],
                "error": error,
                "backup": backup.name,
                "map": after.public(),
            }

    async def _send(self, robot: RobotSource, op: diff.Op) -> None:
        if op.kind == "delete":
            if op.id is None:
                raise ValueError("no id to delete")
            deleters = {
                "nogozones": robot.delete_nogozone,
                "pathways": robot.delete_pathway,
                "sidewalks": robot.delete_sidewalk,
            }
            await deleters[op.family](op.id)
            return
        if op.family == "areas":
            if op.id is None:
                raise ValueError("areas cannot be created here")
            await robot.update_area(
                op.id,
                name=op.name if "name" in op.changes else None,
                outline=op.points if "shape" in op.changes else None,
                algorithm_type=op.algorithm_type,
            )
            return
        if op.points is None or op.ref is None:
            raise ValueError("nothing to send")
        if op.family == "nogozones":
            await robot.save_nogozone(
                op.name, op.points, op.ref, zone_id=op.id, enabled=op.enabled
            )
        elif op.family == "pathways":
            await robot.save_pathway(op.name, op.points, op.ref, pathway_id=op.id)
        elif op.family == "sidewalks":
            await robot.save_sidewalk(op.name, op.points, op.ref, sidewalk_id=op.id)
        else:
            raise ValueError(f"cannot save {op.family}")
