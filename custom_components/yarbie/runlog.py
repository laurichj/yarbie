"""yarbie's own record of each run it watched, one JSON file per run.

Kept under ``/config/yarbie/runs`` so Home Assistant's backups include it. The robot keeps
its own history (see ``editor/runs.py``); this adds what only an observer knows: why a run
ended, pauses and faults on the way, rain, who started it, and the map it ran on.
"""

from __future__ import annotations

from collections.abc import Callable
import json
import logging
from pathlib import Path
import time
from typing import Any
import uuid

from ._vendor.yarbo_local.lifecycle import EventKind, LifecycleEvent
from ._vendor.yarbo_local.models import RobotState

_LOGGER = logging.getLogger(__name__)

DEFAULT_RETENTION_DAYS = 365
# A start yarbie sent counts as who started the run that begins within this.
ATTRIBUTE_S = 120.0


class RunLog:
    """Files on disk. Blocking: call from an executor."""

    def __init__(self, root: Path, retention_days: int = DEFAULT_RETENTION_DAYS) -> None:
        self.dir = root / "runs"
        self.retention_s = retention_days * 86400

    def save(self, entry: dict[str, Any]) -> None:
        self.dir.mkdir(parents=True, exist_ok=True)
        path = self.dir / f"{int(entry['started'])}.json"
        # A name of its own per write, so two saves of one run never share a temp file.
        tmp = path.with_name(f".{path.stem}.{uuid.uuid4().hex}.tmp")
        tmp.write_text(json.dumps(entry, indent=1, sort_keys=True), encoding="utf-8")
        tmp.replace(path)

    def entries(self) -> list[dict[str, Any]]:
        if not self.dir.exists():
            return []
        out = []
        for path in sorted(self.dir.glob("*.json")):
            try:
                out.append(json.loads(path.read_text(encoding="utf-8")))
            except (OSError, ValueError) as err:
                _LOGGER.warning("skipping unreadable run record %s: %s", path.name, err)
        return out

    def prune(self, now: float | None = None) -> int:
        if not self.dir.exists():
            return 0
        cutoff = (now or time.time()) - self.retention_s
        removed = 0
        for path in self.dir.glob("*.json"):
            if path.stem.isdigit() and int(path.stem) < cutoff:
                path.unlink(missing_ok=True)
                removed += 1
        return removed


class RunRecorder:
    """Turns lifecycle events and state frames into run log entries.

    ``save`` is called with an entry whenever it changes enough to keep (start, pause,
    resume, finish); the caller writes it off the event loop.
    """

    def __init__(
        self,
        save: Callable[[dict[str, Any]], None],
        plan_info: Callable[[int | None], tuple[str | None, list[int]]],
        map_revision: Callable[[], str | None],
    ) -> None:
        self._save = save
        self._plan_info = plan_info
        self._map_revision = map_revision
        self.current: dict[str, Any] | None = None
        self.last: dict[str, Any] | None = None
        self._start_request: tuple[float, str] | None = None

    def note_start(self, who: str) -> None:
        """yarbie is about to start a plan on someone's behalf."""
        self._start_request = (time.time(), who)

    def clear_start(self) -> None:
        """The start was refused or failed: a run that begins now is someone else's."""
        self._start_request = None

    def _started_by(self, at: float) -> str:
        request, self._start_request = self._start_request, None
        if request is not None and at - request[0] <= ATTRIBUTE_S:
            return request[1]
        return "the Yarbo app or a schedule"

    def _open(self, event: LifecycleEvent, state: RobotState, adopted: bool) -> dict[str, Any]:
        started = event.at
        if event.run_id and "-" in event.run_id:
            robot_start = event.run_id.rsplit("-", 1)[1]
            if robot_start.isdigit():
                started = float(robot_start)  # the robot's own clock, as its history has it
        name, areas = self._plan_info(event.plan_id)
        return {
            "run_id": event.run_id,
            "plan_id": event.plan_id,
            "plan": name,
            "area_ids": areas,
            "started": int(started),
            "ended": None,
            "reason": None,
            "progress": None,
            "battery_start": None if adopted else state.battery,
            "battery_end": None,
            "map_revision": self._map_revision(),
            "started_by": "already running when yarbie started" if adopted else self._started_by(event.at),
            "events": [],
            "faults": [],
            "rain": False,
        }

    def on_event(self, event: LifecycleEvent, state: RobotState) -> None:
        run = self.current
        if run is not None and event.kind is EventKind.STARTED and run.get("run_id") != event.run_id:
            # The end of the last run was missed (HA restarted mid-run, say): close it.
            run["reason"] = run["reason"] or "unknown"
            run["ended"] = run["ended"] or int(event.at)
            self._save(dict(run))
            self.last, run, self.current = run, None, None
        if run is None:
            # A pause or finish for a run already under way when yarbie started.
            run = self.current = self._open(event, state, adopted=event.kind is not EventKind.STARTED)
        elif event.run_id and not run.get("run_id"):
            run["run_id"] = event.run_id
        record = {"at": int(event.at), "kind": event.kind.value}
        for key in ("reason", "progress", "fault_code", "fault"):
            value = getattr(event, key)
            if value is not None:
                record[key] = value
        run["events"].append(record)
        if event.fault_code is not None and event.fault_code not in run["faults"]:
            run["faults"].append(event.fault_code)
        if event.kind is EventKind.FINISHED:
            run["ended"] = int(event.at)
            run["reason"] = event.reason
            run["progress"] = event.progress
            run["battery_end"] = state.battery
            self.last, self.current = run, None
        self._save(dict(run))

    def on_state(self, state: RobotState) -> None:
        run = self.current
        if run is None:
            return
        rain = state.get("mower_head_info01.rain_sensor")
        if isinstance(rain, int | float) and rain and not run["rain"]:
            run["rain"] = True
            self._save(dict(run))
        if state.error_code and state.error_code not in run["faults"]:
            run["faults"].append(state.error_code)
            self._save(dict(run))
