"""Run history: the robot's records, yarbie's log, and the two joined."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.yarbie._vendor.yarbo_local.lifecycle import EventKind, LifecycleEvent
from custom_components.yarbie._vendor.yarbo_local.models import RobotState
from custom_components.yarbie.editor import runs
from custom_components.yarbie.runlog import RunLog, RunRecorder
from homeassistant.core import HomeAssistant

from conftest import WS, state

FIXTURE = Path(__file__).parents[2] / "third_party/yarbo-local/protocol/fixtures/3.14.11/verify-reads.jsonl"


def _captured_history() -> list[dict[str, Any]]:
    """The 11 runs a real robot reported (firmware 3.14.11)."""

    def find(o: Any) -> list[dict[str, Any]] | None:
        if isinstance(o, dict):
            data = o.get("data")
            if isinstance(data, list) and data and isinstance(data[0], dict) and "actualCleanArea" in data[0]:
                return data
            for v in o.values():
                if (got := find(v)) is not None:
                    return got
        return None

    for line in FIXTURE.read_text(encoding="utf-8").splitlines():
        if (got := find(json.loads(line))) is not None:
            return got
    raise AssertionError("no plan history in the fixture")


def test_robot_records_summarised() -> None:
    merged = runs.merge(_captured_history(), [])
    # Records 3 and 4 are the same run stored twice; the later one is kept.
    assert len(merged) == 10 and [r["robot_id"] for r in merged].count(3) == 0
    newest = merged[0]
    assert newest["plan"] == "west lawn plan" and newest["outcome"] == "completed"
    assert newest["finished"] == {"9": {"fill": True, "edge": True}}
    long_run = next(r for r in merged if r["robot_id"] == 2)
    assert long_run["outcome"] == "partial" and long_run["progress"] == 79
    # Segments 93→93, 93→89, 89→89, 89→40, 38→38: charging between them is not use.
    assert long_run["charge_trips"] == 4 and long_run["battery_used"] == 53
    assert runs.area_states(newest) == {"9": "done"}
    assert runs.area_states(long_run) == {"4": "left"}


def _event(kind: EventKind, at: float, **kw: Any) -> LifecycleEvent:
    return LifecycleEvent(kind, at, kw.pop("run_id", "1-1789837885"), kw.pop("plan_id", 1), **kw)


def test_recorder_follows_a_run_and_joins_the_robot_record() -> None:
    west = _captured_history()[-1]["plan"]["id"]
    saved: list[dict[str, Any]] = []
    recorder = RunRecorder(saved.append, lambda _p: ("west lawn plan", [9]), lambda: "rev1")
    state = RobotState(raw={"BatteryMSG": {"capacity": 96}})
    recorder.note_start("Josh")
    recorder.on_event(_event(EventKind.STARTED, 1789837890, run_id=f"{west}-1789837885", plan_id=west), state)
    recorder.on_event(_event(EventKind.PAUSED, 1789840000, run_id=f"{west}-1789837885", plan_id=west, reason="fault", fault_code=901, fault="stuck"), state)
    recorder.on_event(_event(EventKind.RESUMED, 1789840300, run_id=f"{west}-1789837885", plan_id=west), state)
    recorder.on_state(RobotState(raw={"mower_head_info01": {"rain_sensor": 1}}))
    recorder.on_event(_event(EventKind.FINISHED, 1789845978, run_id=f"{west}-1789837885", plan_id=west, reason="completed", progress=100.0), state)
    entry = saved[-1]
    # The robot's own start time (from the run id), so it matches the robot's record.
    assert entry["started"] == 1789837885 and entry["started_by"] == "Josh"
    assert [e["kind"] for e in entry["events"]] == ["plan_started", "plan_paused", "plan_resumed", "plan_finished"]
    assert entry["faults"] == [901] and entry["rain"] and entry["reason"] == "completed"
    assert recorder.current is None and recorder.last == entry

    merged = runs.merge(_captured_history(), [entry])
    newest = merged[0]
    assert newest["source"] == ["robot", "yarbie"] and newest["started_by"] == "Josh"
    assert newest["faults"] == [901] and newest["map_revision"] == "rev1"


def test_runs_started_elsewhere_and_missed_ends() -> None:
    saved: list[dict[str, Any]] = []
    recorder = RunRecorder(saved.append, lambda _p: (None, []), lambda: None)
    state = RobotState(raw={})
    recorder.on_event(_event(EventKind.STARTED, 100, run_id="2-100", plan_id=2), state)
    assert saved[-1]["started_by"] == "the Yarbo app or a schedule"
    # HA was down for the end; the next start closes the old run.
    recorder.on_event(_event(EventKind.STARTED, 5000, run_id="2-5000", plan_id=2), state)
    closed = next(e for e in saved if e["started"] == 100 and e["ended"] is not None)
    assert closed["reason"] == "unknown"
    # A finish for a run already under way when yarbie started.
    fresh = RunRecorder(saved.append, lambda _p: (None, []), lambda: None)
    fresh.on_event(_event(EventKind.FINISHED, 9000, run_id="3-8000", plan_id=3, reason="stopped"), state)
    assert saved[-1]["started"] == 8000 and saved[-1]["started_by"] == "already running when yarbie started"
    # Seen only by yarbie: listed from the log alone.
    merged = runs.merge([], [saved[-1]])
    assert merged[0]["outcome"] == "stopped" and merged[0]["source"] == ["yarbie"]


def test_run_log_files(tmp_path: Path) -> None:
    log = RunLog(tmp_path, retention_days=30)
    log.save({"started": 1000, "plan": "old"})
    log.save({"started": 2_000_000_000, "plan": "new"})
    assert [e["plan"] for e in log.entries()] == ["old", "new"]
    assert log.prune(now=2_000_000_000) == 1
    assert [e["plan"] for e in log.entries()] == ["new"]


async def test_runs_api_and_last_run_sensors(hass: HomeAssistant, ws: WS, entry: MockConfigEntry) -> None:
    await entry.runtime_data.async_refresh_runs(refresh=True)
    await hass.async_block_till_done()
    listed = (await ws("yarbie/runs/list"))["result"]
    assert [r["plan"] for r in listed["runs"]] == ["Whole yard", "Front only", "Whole yard"]
    assert {p["name"] for p in listed["plans"]} == {"Whole yard", "Front only"}
    front = (await ws("yarbie/runs/list", plan_id=2))["result"]["runs"]
    assert len(front) == 1 and front[0]["outcome"] == "partial"

    newest = (await ws("yarbie/runs/get", key=listed["runs"][0]["key"]))["result"]
    assert newest["area_states"] == {"1": "done", "2": "done"} and newest["charge_trips"] == 1
    assert len(newest["segments"]) == 2
    missing = await ws("yarbie/runs/get", key="robot:999:1")
    assert missing["error"]["code"] == "not_found"

    assert state(hass, "sensor.yarbo_last_run").state == "completed"
    # The key the dashboard's ?run= link takes.
    assert state(hass, "sensor.yarbo_last_run").attributes["run"] == listed["runs"][0]["key"]
    assert state(hass, "sensor.yarbo_last_run_progress").state == "100"
    assert state(hass, "sensor.yarbo_last_run_work_time").state == str(round((3300 + 800) / 60, 1))
