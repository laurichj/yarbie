"""What the PR #1 review found: starts mid-run, run log writes, apply's read-back,
start attribution, the runs plan list, and route previews mid-run."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
from typing import Any

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry, async_capture_events

from custom_components.yarbie.const import OPT_OBSERVE_ONLY
from custom_components.yarbie.runlog import RunLog
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError

from conftest import WS


async def _mid_run(entry: MockConfigEntry) -> None:
    """Put the simulator in a running plan (planning code 1) and let the session hear it."""
    coordinator = entry.runtime_data
    sim = coordinator.robot.simulator
    assert sim is not None
    sim.snapshot["StateMSG"]["on_going_planning"] = 1
    await coordinator.robot.snapshot()
    assert coordinator.robot.busy


async def test_start_is_refused_while_a_plan_runs(hass: HomeAssistant, entry: MockConfigEntry) -> None:
    """HA shows a waiting run as paused and offers start; a start would replace the run."""
    await _mid_run(entry)
    sim = entry.runtime_data.robot.simulator
    started: list[Any] = []
    sim.cmd_start_plan = started.append  # type: ignore[method-assign]
    with pytest.raises(HomeAssistantError, match="already running"):
        await hass.services.async_call("lawn_mower", "start_mowing", {"entity_id": "lawn_mower.yarbo"}, blocking=True)
    with pytest.raises(HomeAssistantError, match="already running"):
        await hass.services.async_call("yarbie", "start_plan", {"plan": "Front only"}, blocking=True)
    assert started == []


@pytest.mark.parametrize("options", [{OPT_OBSERVE_ONLY: True}])
async def test_a_refused_start_is_not_credited(hass: HomeAssistant, entry: MockConfigEntry) -> None:
    with pytest.raises(HomeAssistantError):
        await hass.services.async_call("yarbie", "start_plan", {"plan": "Front only"}, blocking=True)
    # A run the app or a schedule starts next is not this user's.
    assert entry.runtime_data.recorder._start_request is None


def test_concurrent_saves_of_one_run(tmp_path: Path) -> None:
    log = RunLog(tmp_path)
    with ThreadPoolExecutor(8) as pool:
        list(pool.map(lambda n: log.save({"started": 1000, "n": n}), range(200)))
    assert [e["started"] for e in log.entries()] == [1000]
    assert not list((tmp_path / "runs").glob("*.tmp"))


async def test_run_log_writes_land_in_order(hass: HomeAssistant, entry: MockConfigEntry) -> None:
    coordinator = entry.runtime_data
    for n in range(30):
        coordinator._save_run({"started": 5000, "n": n})
    await hass.async_block_till_done()
    path = coordinator.runlog.dir / "5000.json"
    assert json.loads(path.read_text())["n"] == 29


async def test_apply_keeps_its_record_when_the_read_back_fails(
    hass: HomeAssistant, ws: WS, entry: MockConfigEntry
) -> None:
    changed = async_capture_events(hass, "yarbie_map_changed")
    body = (await ws("yarbie/map"))["result"]
    fc = body["features"]
    next(f for f in fc["features"] if f["id"] == "areas:1")["properties"]["name"] = "Front"
    service = entry.runtime_data.service
    real_load = service.load
    calls = 0

    async def flaky_load() -> Any:
        nonlocal calls
        calls += 1
        if calls == 2:  # the read-back after sending
            raise TimeoutError("map read timed out")
        return await real_load()

    service.load = flaky_load  # type: ignore[method-assign]
    reply = await ws("yarbie/apply", features=fc, revision=body["revision"])
    assert reply["success"], reply
    result = reply["result"]
    assert len(result["applied"]) == 1 and result["backup"] and result["map"] is None
    assert "reload" in result["reload"]
    await hass.async_block_till_done()
    assert [e.data["backup"] for e in changed] == [result["backup"]]


async def test_runs_plan_list_ignores_the_filter(ws: WS, entry: MockConfigEntry) -> None:
    await entry.runtime_data.async_refresh_runs(refresh=True)
    filtered = (await ws("yarbie/runs/list", plan_id=2))["result"]
    assert {p["name"] for p in filtered["plans"]} == {"Whole yard", "Front only"}
    assert {r["plan"] for r in filtered["runs"]} == {"Front only"}


async def test_route_preview_is_refused_mid_run(ws: WS, entry: MockConfigEntry) -> None:
    await _mid_run(entry)
    reply = await ws("yarbie/route/start", plan_id=1)
    assert reply["error"]["code"] == "conflict"
