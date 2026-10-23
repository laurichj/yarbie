"""Reading the map and applying edits, with the safety checks around every write."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from .._vendor.yarbo_local.exceptions import YarboError
from .._vendor.yarbo_local.payloads import APP_MOWER_AREA_KEYS
from . import diff, heatmap, mapdoc, smooth
from .backups import BackupStore

if TYPE_CHECKING:
    from .._vendor.yarbo_local.models import Schedule
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


# The mowing settings the app sends that are the area's own (the rest the robot keeps).
AREA_SETTING_KEYS = frozenset(APP_MOWER_AREA_KEYS) - {"id", "mapping_module_type"}


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

    async def history(self, refresh: bool = False) -> list[dict[str, Any]]:
        """The robot's run history (read once, then on request: it is large)."""
        if refresh or self._history is None:
            self._history = await self.robot.plan_history()
        return self._history

    async def heatmap(self, days: int | None, refresh: bool) -> dict[str, Any]:
        """Estimated wear from the robot's run history."""
        history = await self.history(refresh)
        view = await self.load()
        return await asyncio.to_thread(
            heatmap.estimate, view.raw, history, view.reference, since=heatmap.window(days)
        )

    async def preview(self, edited: dict[str, Any]) -> list[diff.Op]:
        view = await self.load()
        return diff.compute(view.raw, view.reference, edited)

    async def smooth(
        self, edited: dict[str, Any], ids: list[str], tolerance: float, *, arcs: bool, keep_shared: bool
    ) -> list[dict[str, Any]]:
        """Proposed outlines only: nothing is sent. The map last read will do for the
        references and the dock, so this does not ask the robot again."""
        view = self.last or await self.load()
        return await asyncio.to_thread(
            smooth.smooth_features,
            view.raw,
            view.reference,
            edited,
            ids,
            tolerance,
            arcs=arcs,
            keep_shared=keep_shared,
        )

    def _check_idle(self) -> None:
        if self.robot.busy:
            raise ConflictError("The robot is running or paused in a plan. Stop it first.")

    async def apply(self, edited: dict[str, Any], revision: str, actor: str | None) -> dict[str, Any]:
        self._check_writes()
        async with self.lock:
            view = await self.load()
            if view.revision != revision:
                raise ConflictError(
                    "The map on the robot changed since you loaded it (perhaps in the app). "
                    "Reload, then make your edits again."
                )
            self._check_idle()
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
            result: dict[str, Any] = {
                "applied": applied,
                "skipped": [o.public() for o in ops if not o.supported],
                "error": error,
                "backup": backup.name,
                "map": None,
            }
            # The changes are on the robot now; a failed read-back must not lose the
            # record of them (or the backup's name).
            try:
                result["map"] = (await self.load()).public()
            except Exception as err:  # noqa: BLE001 - reported with the result
                result["reload"] = f"sent, but reading the map back failed ({err}); reload it"
            return result

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
            self._check_idle()
            return await self.robot.save_plan(name, area_ids, plan_id)

    async def delete_plan(self, plan_id: int) -> None:
        self._check_writes()
        async with self.lock:
            self._check_idle()
            await self.robot.delete_plan(plan_id)

    async def save_schedule(self, **fields: Any) -> Schedule:
        self._check_writes()
        async with self.lock:
            self._check_idle()
            return await self.robot.save_schedule(**fields)

    async def delete_schedule(self, schedule_id: int) -> None:
        self._check_writes()
        async with self.lock:
            self._check_idle()
            await self.robot.delete_schedule(schedule_id)

    async def update_mower_area_params(self, area_id: int, changes: dict[str, Any]) -> dict[str, Any]:
        self._check_writes()
        async with self.lock:
            self._check_idle()
            return await self.robot.update_mower_area_params(area_id, changes)

    # -- many areas' settings at once (the Area settings view)

    async def area_settings(self) -> list[dict[str, Any]]:
        """Every work area with its pattern and mowing settings, read from the robot."""
        view = await self.load()
        out = []
        for area in view.raw.get("areas") or []:
            area_id = area.get("id")
            if not isinstance(area_id, int):
                continue
            params = await self.robot.mower_area_params(area_id)
            out.append(
                {
                    "id": area_id,
                    "name": area.get("name") or "",
                    "algorithm_type": area.get("algorithm_type"),
                    "params": params,
                }
            )
        return out

    async def apply_area_settings(
        self, changes: list[dict[str, Any]], actor: str | None
    ) -> dict[str, Any]:
        """Send each area its changes, after backing up every affected area as it was.

        Each change is ``{"area_id", "algorithm_type"?, "params"?}``; ``params`` is a
        partial mowing record, nested values merged into the stored ones. Every change is
        checked before anything is sent. One area failing does not stop the others.

        The reply has each area's result (``ok`` only when something was sent), and the
        changed areas read again, or ``areas`` None with ``read_error`` if that read
        failed: what was sent stays sent.
        """
        self._check_writes()
        for change in changes:
            unknown = sorted(set(change.get("params") or {}) - AREA_SETTING_KEYS)
            if unknown:
                raise ValueError(f"not an area setting the app sends: {', '.join(unknown)}")
        async with self.lock:
            self._check_idle()
            view = await self.load()
            areas = {a.get("id"): a for a in view.raw.get("areas") or []}
            missing = sorted(c["area_id"] for c in changes if c["area_id"] not in areas)
            if missing:
                raise ValueError(f"the robot has no area {', '.join(map(str, missing))}")
            # One read at a time: the session pairs replies with requests by command name.
            before = []
            for change in changes:
                params = await self.robot.mower_area_params(change["area_id"])
                if change.get("params"):
                    _check_nested(change["area_id"], change["params"], params)
                before.append({"area": areas[change["area_id"]], "params": params})
            backup = await asyncio.to_thread(self.backups.save_area_settings, before, actor)
            results: list[dict[str, Any]] = []
            for change in changes:
                area_id = change["area_id"]
                result: dict[str, Any] = {
                    "area_id": area_id, "name": areas[area_id].get("name") or "", "ok": False,
                    "error": None, "skipped": None,
                }
                results.append(result)
                if self.robot.busy:  # started from the app meanwhile: send nothing more
                    result["error"] = "not sent: the robot started a plan"
                    continue
                pattern = change.get("algorithm_type")
                new_pattern = pattern is not None and pattern != areas[area_id].get("algorithm_type")
                if not new_pattern and not change.get("params"):
                    result["skipped"] = "nothing to change"
                    continue
                try:
                    if new_pattern:
                        await self.robot.update_area(area_id, name=None, outline=None, algorithm_type=pattern)
                    if change.get("params"):
                        await self.robot.update_mower_area_params(area_id, change["params"])
                except Exception as err:  # report it with this area, and go on to the next
                    result["error"] = str(err) or type(err).__name__
                    continue
                result["ok"] = True
            reply: dict[str, Any] = {"results": results, "backup": backup.name, "areas": None, "read_error": None}
            try:
                reply["areas"] = await self._read_back([r["area_id"] for r in results if r["ok"] or r["error"]])
            except (YarboError, TimeoutError) as err:
                reply["read_error"] = str(err) or type(err).__name__
            return reply

    async def _read_back(self, area_ids: list[int]) -> list[dict[str, Any]]:
        """The map once (for patterns), then the settings of the areas that were sent."""
        view = await self.load()
        records = {a.get("id"): a for a in view.raw.get("areas") or []}
        out = []
        for area_id in area_ids:
            record = records.get(area_id, {})
            out.append(
                {
                    "id": area_id,
                    "name": record.get("name") or "",
                    "algorithm_type": record.get("algorithm_type"),
                    "params": await self.robot.mower_area_params(area_id),
                }
            )
        return out


def _check_nested(area_id: int, changes: dict[str, Any], stored: dict[str, Any] | None) -> None:
    """A change to a group of settings (``first_clean_params``...) may only name keys the
    robot already holds in it; ``changed_record`` merges groups without checking."""
    for key, value in changes.items():
        held = (stored or {}).get(key)
        if isinstance(value, dict) != isinstance(held, dict):
            if stored is None:
                raise ValueError(f"the robot sent no settings for area {area_id}")
            kind = "a group of settings" if isinstance(held, dict) else "a single setting"
            raise ValueError(f"{key} of area {area_id} is {kind}")
        if isinstance(value, dict) and isinstance(held, dict):
            unknown = sorted(set(value) - set(held))
            if unknown:
                raise ValueError(f"not a setting in {key}: {', '.join(unknown)}")
