"""The Area settings view: every area's settings at once, changed in bulk, backed up first."""

from __future__ import annotations

import json

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry, async_capture_events

from custom_components.yarbie.const import OPT_ALLOW_WRITES
from homeassistant.core import HomeAssistant

from conftest import WS, busy


async def test_get_lists_every_area_with_its_settings(ws: WS) -> None:
    got = (await ws("yarbie/area_settings/get"))["result"]
    heights = {a["name"]: a["params"]["first_clean_params"]["blade_height"] for a in got["areas"]}
    assert heights == {"Front Lawn": 50, "Back Lawn": 64}
    assert {a["algorithm_type"] for a in got["areas"]} == {0}
    assert "first_clean_params" in got["editable"] and "id" not in got["editable"]


async def test_apply_to_several_areas(hass: HomeAssistant, ws: WS, entry: MockConfigEntry) -> None:
    changed = async_capture_events(hass, "yarbie_area_settings_changed")
    sim = entry.runtime_data.robot.simulator
    reply = await ws(
        "yarbie/area_settings/apply",
        changes=[
            {"area_id": 1, "params": {"first_clean_params": {"blade_height": 60}, "edge_circles": 1}},
            {"area_id": 2, "params": {"first_clean_params": {"blade_height": 60}}, "algorithm_type": 4},
        ],
    )
    assert reply["success"], reply
    result = reply["result"]
    assert [(r["area_id"], r["ok"], r["error"]) for r in result["results"]] == [(1, True, None), (2, True, None)]
    # Nested values are merged into what the robot holds, not replacing it.
    assert sim.mower_area_params[1]["first_clean_params"]["blade_height"] == 60
    assert sim.mower_area_params[1]["first_clean_params"]["route_angle"] == 10
    assert sim.mower_area_params[1]["edge_circles"] == 1
    assert sim.mower_area_params[2]["edge_circles"] == 2
    assert next(a for a in sim.site_map["areas"] if a["id"] == 2)["algorithm_type"] == 4
    # The reply carries the table read again.
    fresh = {a["id"]: a for a in result["areas"]}
    assert fresh[2]["algorithm_type"] == 4 and fresh[2]["params"]["first_clean_params"]["blade_height"] == 60

    # A backup of each area as it was, beside the map backups, naming who sent it.
    store = entry.runtime_data.service.backups
    path = store.dir / result["backup"]
    assert "before-area-settings" in path.name
    saved = json.loads(path.read_text())
    assert saved["actor"]
    before = {b["area"]["id"]: b for b in saved["areas"]}
    assert before[1]["params"]["first_clean_params"]["blade_height"] == 50
    assert before[2]["params"]["first_clean_params"]["blade_height"] == 64
    assert before[2]["area"]["algorithm_type"] == 0
    listed = (await ws("yarbie/backups/list"))["result"]
    assert [b["kind"] for b in listed] == ["area_settings"]
    # It is not a map: the map history cannot load it.
    assert (await ws("yarbie/backups/get", name=listed[0]["name"]))["error"]["code"] == "not_found"
    await hass.async_block_till_done()
    assert [e.data["areas"] for e in changed] == [[1, 2]]


async def test_one_failing_area_does_not_stop_the_rest(ws: WS, entry: MockConfigEntry) -> None:
    sim = entry.runtime_data.robot.simulator
    original = sim.cmd_save_mower_area_params

    def refuse_front(value: dict) -> None:
        if value.get("id") == 1:
            sim._feedback("save_mower_area_params", -1, "refused", "")
            return
        original(value)

    sim.cmd_save_mower_area_params = refuse_front  # type: ignore[method-assign]
    result = (
        await ws(
            "yarbie/area_settings/apply",
            changes=[{"area_id": a, "params": {"edge_circles": 3}} for a in (1, 2)],
        )
    )["result"]
    outcome = {r["area_id"]: (r["ok"], bool(r["error"])) for r in result["results"]}
    assert outcome == {1: (False, True), 2: (True, False)}
    assert sim.mower_area_params[2]["edge_circles"] == 3


async def test_bad_changes_send_nothing(ws: WS, entry: MockConfigEntry) -> None:
    sim = entry.runtime_data.robot.simulator
    unknown = await ws(
        "yarbie/area_settings/apply",
        changes=[{"area_id": 1, "params": {"edge_circles": 1}}, {"area_id": 2, "params": {"id": 5}}],
    )
    assert unknown["error"]["code"] == "invalid"
    missing = await ws("yarbie/area_settings/apply", changes=[{"area_id": 99, "params": {"edge_circles": 1}}])
    assert missing["error"]["code"] == "invalid"
    assert sim.mower_area_params[1]["edge_circles"] == 2
    assert not entry.runtime_data.service.backups.dir.exists()


async def test_area_settings_wait_for_the_robot_to_stop(
    hass: HomeAssistant, ws: WS, entry: MockConfigEntry
) -> None:
    await busy(hass, entry)
    reply = await ws("yarbie/area_settings/apply", changes=[{"area_id": 1, "params": {"edge_circles": 1}}])
    assert reply["error"]["code"] == "conflict"
    assert entry.runtime_data.robot.simulator.mower_area_params[1]["edge_circles"] == 2


@pytest.mark.parametrize("options", [{OPT_ALLOW_WRITES: False}])
async def test_area_settings_obey_the_kill_switch(ws: WS, entry: MockConfigEntry) -> None:
    reply = await ws("yarbie/area_settings/apply", changes=[{"area_id": 1, "params": {"edge_circles": 1}}])
    assert reply["error"]["code"] == "read_only"
    assert not entry.runtime_data.service.backups.dir.exists()


async def test_nested_keys_are_checked_before_sending(ws: WS, entry: MockConfigEntry) -> None:
    sim = entry.runtime_data.robot.simulator
    reply = await ws(
        "yarbie/area_settings/apply",
        changes=[
            {"area_id": 1, "params": {"edge_circles": 1}},
            {"area_id": 2, "params": {"first_clean_params": {"bogus": 1}}},
        ],
    )
    assert reply["error"]["code"] == "invalid" and "bogus" in reply["error"]["message"]
    flat = await ws("yarbie/area_settings/apply", changes=[{"area_id": 1, "params": {"first_clean_params": 5}}])
    assert flat["error"]["code"] == "invalid"
    assert sim.mower_area_params[1]["edge_circles"] == 2
    assert not entry.runtime_data.service.backups.dir.exists()


async def test_nothing_to_send_is_not_reported_as_a_change(hass: HomeAssistant, ws: WS) -> None:
    changed = async_capture_events(hass, "yarbie_area_settings_changed")
    reply = await ws("yarbie/area_settings/apply", changes=[{"area_id": 1, "algorithm_type": 0}])
    [result] = reply["result"]["results"]
    assert (result["ok"], result["error"], result["skipped"]) == (False, None, "nothing to change")
    await hass.async_block_till_done()
    assert changed == []


async def test_a_failed_read_back_still_reports_what_was_sent(ws: WS, entry: MockConfigEntry) -> None:
    async def no_answer(area_ids: list[int]) -> list[dict]:
        raise TimeoutError("no reply")

    entry.runtime_data.service._read_back = no_answer  # type: ignore[method-assign]
    reply = await ws("yarbie/area_settings/apply", changes=[{"area_id": 1, "params": {"edge_circles": 1}}])
    assert reply["success"], reply
    result = reply["result"]
    assert result["areas"] is None and result["read_error"] == "no reply"
    assert result["results"][0]["ok"] and result["backup"]
    assert entry.runtime_data.robot.simulator.mower_area_params[1]["edge_circles"] == 1
