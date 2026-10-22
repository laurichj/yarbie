"""Reading the map and applying edits, with the safety checks around every write."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from . import diff, heatmap, mapdoc
from .backups import BackupStore

if TYPE_CHECKING:
    from ..robot import Robot


class ConflictError(RuntimeError):
    """The robot's map or state does not allow this write right now."""


class ReadOnlyError(RuntimeError):
    """Writes are turned off (observe only, the kill switch, or untested firmware)."""


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
    def __init__(
        self,
        robot: Robot,
        backups: BackupStore,
        lock: asyncio.Lock,
        check_writes: Callable[[], None],
    ) -> None:
        self.robot = robot
        self.backups = backups
        self.lock = lock  # one write at a time, shared with plan and settings writes
        self._check_writes = check_writes
        self.last: MapView | None = None
        self._history: list[dict[str, Any]] | None = None

    @property
    def last_reference(self) -> mapdoc.Ref | None:
        return mapdoc.site_reference(self.last.raw, self.last.reference) if self.last else None

    async def load(self) -> MapView:
        raw = await self.robot.raw_map()
        reference = await self.robot.reference()
        self.last = MapView(raw, reference, mapdoc.map_revision(raw))
        return self.last

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

    async def apply(self, edited: dict[str, Any], revision: str, actor: str | None) -> dict[str, Any]:
        self._check_writes()
        async with self.lock:
            view = await self.load()
            if view.revision != revision:
                raise ConflictError(
                    "The map on the robot changed since you loaded it (perhaps in the app). "
                    "Reload, then make your edits again."
                )
            if self.robot.busy:
                raise ConflictError("The robot is running or paused in a plan. Stop it first.")
            ops = diff.compute(view.raw, view.reference, edited)
            todo = [op for op in ops if op.supported]
            if not todo:
                return {"applied": [], "skipped": [o.public() for o in ops], "backup": None}
            backup = await asyncio.to_thread(self.backups.save, view.raw, "before-apply", actor)
            applied: list[dict[str, Any]] = []
            error: str | None = None
            for op in todo:
                try:
                    await self._send(op)
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

    async def _send(self, op: diff.Op) -> None:
        robot = self.robot
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

    # -- plans and settings, under the same lock and rails

    async def save_plan(self, name: str, area_ids: list[int], plan_id: int | None) -> int:
        self._check_writes()
        async with self.lock:
            return await self.robot.save_plan(name, area_ids, plan_id)

    async def delete_plan(self, plan_id: int) -> None:
        self._check_writes()
        async with self.lock:
            await self.robot.delete_plan(plan_id)

    async def update_mower_area_params(self, area_id: int, changes: dict[str, Any]) -> dict[str, Any]:
        self._check_writes()
        async with self.lock:
            if self.robot.busy:
                raise ConflictError("The robot is running or paused in a plan. Stop it first.")
            return await self.robot.update_mower_area_params(area_id, changes)
