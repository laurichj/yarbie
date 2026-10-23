"""Run history: the robot's own records, joined with what yarbie saw happen.

The robot keeps one record per finished run (``read_all_plan_history``): the plan, when
each work segment started and ended (one per trip to the charger), the battery at each,
the area mowed against the plan's, and which areas it finished. It does not say why a
run ended (``finishType`` and ``startType`` read 0 for every run seen) or who started it.

yarbie's own log fills that in for runs it watched: the reason it ended, pauses and
faults, rain, who started it, and the map it ran on. Runs from before yarbie was set up,
or while Home Assistant was down, have the robot's record only.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from typing import Any

# A log entry belongs to a robot record when they started within this of each other.
MATCH_S = 180
# The robot reports progress as a whole percent; at or above this a run is complete.
COMPLETE_PCT = 99


def _num(value: Any) -> float | None:
    return float(value) if isinstance(value, int | float) and not isinstance(value, bool) else None


def _int_list(value: Any) -> list[int]:
    return [int(v) for v in value or [] if isinstance(v, int | float) and not isinstance(v, bool)]


def finished_parts(record: Mapping[str, Any]) -> dict[int, dict[str, bool]]:
    """Per area, whether its fill and its edge pass were finished. ``finishIds`` holds
    records ({id, move_type}: 1 the fill, 0 the edge) or, on older firmware, bare ids."""
    out: dict[int, dict[str, bool]] = {}
    for done in record.get("finishIds") or []:
        if isinstance(done, Mapping) and isinstance(done.get("id"), int):
            part = out.setdefault(done["id"], {"fill": False, "edge": False})
            part["fill" if done.get("move_type") == 1 else "edge"] = True
        elif isinstance(done, int):
            out[done] = {"fill": True, "edge": True}
    return out


def summarize(record: Mapping[str, Any]) -> dict[str, Any] | None:
    """One robot record as the panel shows it; None if it has no start time."""
    started = _num(record.get("startTime"))
    if started is None:
        return None
    raw_plan = record.get("plan")
    plan: Mapping[str, Any] = raw_plan if isinstance(raw_plan, Mapping) else {}
    starts = _int_list(record.get("startTimeV")) or [int(started)]
    ends = _int_list(record.get("endTimeV"))
    battery_start = _int_list(record.get("start_battery_capacity"))
    battery_end = _int_list(record.get("end_battery_capacity"))
    segments = []
    for i, start in enumerate(starts):
        segments.append(
            {
                "start": start,
                "end": ends[i] if i < len(ends) else None,
                "battery_start": battery_start[i] if i < len(battery_start) else None,
                "battery_end": battery_end[i] if i < len(battery_end) else None,
            }
        )
    used = sum(
        s["battery_start"] - s["battery_end"]
        for s in segments
        if s["battery_start"] is not None and s["battery_end"] is not None
    )
    progress = _num(record.get("schedule_end_percent"))
    if progress is not None and progress.is_integer():
        progress = int(progress)
    done = finished_parts(record)
    area_ids = _int_list(plan.get("areaIds"))
    complete = (progress is not None and progress >= COMPLETE_PCT) or (
        bool(area_ids) and all(done.get(a, {}).get("fill") for a in area_ids)
    )
    ended = _num(record.get("endTime"))
    return {
        "key": f"robot:{record.get('id')}:{int(started)}",
        "robot_id": record.get("id"),
        "plan_id": plan.get("id"),
        "plan": plan.get("name"),
        "area_ids": area_ids,
        "started": int(started),
        "ended": int(ended) if ended is not None else None,
        "work_s": _num(record.get("duration")),
        "progress": progress,
        "mowed_m2": _num(record.get("actualCleanArea")),
        "total_m2": _num(record.get("totalCleanArea")),
        "segments": segments,
        "charge_trips": max(0, len(segments) - 1),
        "battery_used": used if segments else None,
        "finished": {str(k): v for k, v in done.items()},
        "outcome": "completed" if complete else "partial",
        "source": ["robot"],
    }


def from_log(entry: Mapping[str, Any]) -> dict[str, Any]:
    """A run yarbie watched but the robot has no record of (yet, or any more)."""
    return {
        "key": f"log:{entry.get('started')}",
        "robot_id": None,
        "plan_id": entry.get("plan_id"),
        "plan": entry.get("plan"),
        "area_ids": list(entry.get("area_ids") or []),
        "started": entry.get("started"),
        "ended": entry.get("ended"),
        "work_s": None,
        "progress": entry.get("progress"),
        "mowed_m2": None,
        "total_m2": None,
        "segments": [],
        "charge_trips": entry.get("charge_trips"),
        "battery_used": _used(entry),
        "finished": {},
        "outcome": _outcome(entry, None),
        "source": ["yarbie"],
    }


def _used(entry: Mapping[str, Any]) -> float | None:
    start, end = _num(entry.get("battery_start")), _num(entry.get("battery_end"))
    return start - end if start is not None and end is not None else None


def _outcome(entry: Mapping[str, Any], robot: Mapping[str, Any] | None) -> str:
    """Why it ended: yarbie's reason when it saw the end, else what the robot's numbers say."""
    reason = entry.get("reason")
    if reason == "completed" or (robot is not None and robot.get("outcome") == "completed"):
        return "completed"
    if isinstance(reason, str) and reason:
        return reason
    if entry.get("ended") is None:
        return "running" if robot is None else str(robot.get("outcome"))
    return robot.get("outcome", "partial") if robot else "partial"


def merge(
    history: Iterable[Mapping[str, Any]], log: Sequence[Mapping[str, Any]]
) -> list[dict[str, Any]]:
    """Every run, newest first: robot records with yarbie's log joined in where one
    matches, then log entries the robot does not have."""
    runs: dict[tuple[Any, int], dict[str, Any]] = {}
    for record in history:
        run = summarize(record)
        if run is None:
            continue
        # The robot has been seen to store one run twice (same plan and start, later id
        # slightly different); the later record is kept.
        key = (run["plan_id"], run["started"])
        if key not in runs or (run["robot_id"] or 0) > (runs[key]["robot_id"] or 0):
            runs[key] = run
    out = list(runs.values())
    unmatched: list[Mapping[str, Any]] = []
    for entry in log:
        started = _num(entry.get("started"))
        match = None
        if started is not None:
            candidates = [
                r for r in out
                if abs(r["started"] - started) <= MATCH_S
                and (entry.get("plan_id") is None or r["plan_id"] in (None, entry.get("plan_id")))
            ]
            match = min(candidates, key=lambda r: abs(r["started"] - started), default=None)
        if match is None:
            unmatched.append(entry)
            continue
        _join(match, entry)
    for entry in unmatched:
        run = from_log(entry)
        _join(run, entry)
        out.append(run)
    out.sort(key=lambda r: r["started"] or 0, reverse=True)
    return out


def _join(run: dict[str, Any], entry: Mapping[str, Any]) -> None:
    run["outcome"] = _outcome(entry, run if "robot" in run["source"] else None)
    run["reason"] = entry.get("reason")
    run["events"] = list(entry.get("events") or [])
    run["faults"] = list(entry.get("faults") or [])
    run["rain"] = bool(entry.get("rain"))
    run["started_by"] = entry.get("started_by")
    run["map_revision"] = entry.get("map_revision")
    if run.get("plan") is None:
        run["plan"] = entry.get("plan")
    if "yarbie" not in run["source"]:
        run["source"] = [*run["source"], "yarbie"]


def area_states(run: Mapping[str, Any]) -> dict[str, str]:
    """For the map: each of the run's areas as done (fill finished), edge (only the edge
    pass finished) or left."""
    finished = run.get("finished") or {}
    out = {}
    for area in run.get("area_ids") or []:
        parts = finished.get(str(area)) or {}
        out[str(area)] = "done" if parts.get("fill") else "edge" if parts.get("edge") else "left"
    return out
