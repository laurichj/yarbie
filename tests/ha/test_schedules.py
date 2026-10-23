"""Schedules: the websocket commands, the rails, a switch each, and the calendar.

The simulator stores schedules as the robot did on 3.14.11. Their numbers are sent as
given; the calendar's reading of them is unconfirmed, and its events say so.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.yarbie import schedules
from custom_components.yarbie._vendor.yarbo_local.models import Schedule
from custom_components.yarbie.const import OPT_ALLOW_WRITES
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import entity_registry as er
from homeassistant.util import dt as dt_util

from conftest import WS, busy, state

DEMO = "switch.yarbo_schedule_front_lawn_mornings"


def _new(**overrides: Any) -> dict[str, Any]:
    return {
        "plan_id": 1,
        "name": "Weekend",
        "start_time": "10:00:00",
        "end_time": "12:30:00",
        **overrides,
    }


async def test_list_keeps_the_robots_numbers(ws: WS) -> None:
    got = (await ws("yarbie/schedules/list"))["result"]
    [demo] = got["schedules"]
    assert (demo["week_day"], demo["schedule_type"], demo["return_method"]) == (1, 3, 2)
    assert demo["plan"] == "Front only"
    assert demo["extra"]["timezone"] == ""  # what the robot adds is passed on, not dropped
    assert got["defaults"] == {"schedule_type": 3, "week_day": 1, "return_method": 2}
    assert "unconfirmed" in got["reading"]


async def test_create_edit_delete(hass: HomeAssistant, ws: WS, entry: MockConfigEntry) -> None:
    sim = entry.runtime_data.robot.simulator
    created = await ws("yarbie/schedules/save", **_new(week_day=96))
    assert created["success"], created
    new_id = created["result"]["id"]
    sent = next(s for s in sim.schedules if s["id"] == new_id)
    # New schedules take the app's numbers unless told otherwise; week_day goes as given.
    assert (sent["week_day"], sent["schedule_type"], sent["return_method"]) == (96, 3, 2)
    assert sent["enable"] is True and sent["last_progress"] == 0
    await hass.async_block_till_done()
    assert state(hass, "switch.yarbo_schedule_weekend").state == "on"

    edited = await ws(
        "yarbie/schedules/save", **_new(schedule_id=new_id, name="Weekend", return_method=7, enabled=False)
    )
    assert edited["success"], edited
    assert next(s for s in sim.schedules if s["id"] == new_id)["return_method"] == 7
    await hass.async_block_till_done()
    assert state(hass, "switch.yarbo_schedule_weekend").state == "off"

    deleted = await ws("yarbie/schedules/delete", schedule_id=new_id)
    assert deleted["success"]
    await hass.async_block_till_done()
    assert hass.states.get("switch.yarbo_schedule_weekend") is None
    assert er.async_get(hass).async_get("switch.yarbo_schedule_weekend") is None


async def test_bad_input_is_refused(ws: WS) -> None:
    assert (await ws("yarbie/schedules/save", **_new(plan_id=99)))["error"]["code"] == "invalid"
    assert (await ws("yarbie/schedules/save", **_new(schedule_id=99)))["error"]["code"] == "not_found"
    bad_time = await ws("yarbie/schedules/save", **_new(start_time="9:00"))
    assert not bad_time["success"]


async def test_deleting_a_plan_removes_its_schedule_switch(hass: HomeAssistant, ws: WS) -> None:
    assert state(hass, DEMO).state == "on"
    assert (await ws("yarbie/plans/delete", plan_id=2))["success"]
    await hass.async_block_till_done()
    assert hass.states.get(DEMO) is None


async def test_switch_turns_a_schedule_off(hass: HomeAssistant, entry: MockConfigEntry) -> None:
    sim = entry.runtime_data.robot.simulator
    await hass.services.async_call("switch", "turn_off", {"entity_id": DEMO}, blocking=True)
    stored = sim.schedules[0]
    assert stored["enable"] is False
    # Everything else goes back as the robot held it.
    assert (stored["week_day"], stored["schedule_type"], stored["return_method"]) == (1, 3, 2)
    assert stored["start_time"] == "09:00:00" and stored["name"] == "Front lawn mornings"
    assert state(hass, DEMO).state == "off"
    assert state(hass, DEMO).attributes["week_day"] == 1


async def test_schedule_writes_wait_for_the_robot_to_stop(
    hass: HomeAssistant, ws: WS, entry: MockConfigEntry
) -> None:
    await busy(hass, entry)
    assert (await ws("yarbie/schedules/save", **_new()))["error"]["code"] == "conflict"
    assert (await ws("yarbie/schedules/delete", schedule_id=1))["error"]["code"] == "conflict"
    with pytest.raises(HomeAssistantError, match="running or paused"):
        await hass.services.async_call("switch", "turn_off", {"entity_id": DEMO}, blocking=True)
    assert entry.runtime_data.robot.simulator.schedules[0]["enable"] is True


@pytest.mark.parametrize("options", [{OPT_ALLOW_WRITES: False}])
async def test_schedule_writes_obey_the_kill_switch(hass: HomeAssistant, ws: WS) -> None:
    assert (await ws("yarbie/schedules/save", **_new()))["error"]["code"] == "read_only"
    with pytest.raises(HomeAssistantError, match="turned off"):
        await hass.services.async_call("switch", "turn_off", {"entity_id": DEMO}, blocking=True)


async def test_calendar_shows_enabled_schedules(hass: HomeAssistant, entry: MockConfigEntry) -> None:
    calendar = state(hass, "calendar.yarbo_schedules")
    assert calendar.attributes["message"] == "Front lawn mornings"
    assert "unconfirmed" in calendar.attributes["description"]
    start = dt_util.now()
    response: Any = await hass.services.async_call(
        "calendar",
        "get_events",
        {"entity_id": "calendar.yarbo_schedules", "start_date_time": start, "duration": {"days": 14}},
        blocking=True,
        return_response=True,
    )
    events = response["calendar.yarbo_schedules"]["events"]
    assert len(events) == 2  # week_day 1 read as Monday, two Mondays in a fortnight
    assert all(datetime.fromisoformat(e["start"]).weekday() == 0 for e in events)
    assert all(datetime.fromisoformat(e["start"]).strftime("%H:%M") == "09:00" for e in events)


def _schedule(**raw: Any) -> Schedule:
    record = {"id": 1, "plan_id": 1, "enable": True, "start_time": "17:09:00", "end_time": "16:10:00", "week_day": 1}
    parsed = Schedule.from_wire({**record, **raw})
    assert parsed is not None
    return parsed


def test_reading_of_days_and_times() -> None:
    zone = dt_util.get_default_time_zone()
    monday = datetime(2026, 10, 19, tzinfo=zone)
    week = monday + timedelta(days=7)
    # The app's own enabled schedule ended before it started: read as the next day.
    [(opens, closes)] = schedules.occurrences(_schedule(), monday, week, zone)
    assert opens.isoformat().startswith("2026-10-19T17:09") and closes.isoformat().startswith("2026-10-20T16:10")
    # Saturday and Sunday; the Sunday before runs on into this Monday, so it is there too.
    days = [o.date().isoformat() for o, _ in schedules.occurrences(_schedule(week_day=96), monday, week, zone)]
    assert days == ["2026-10-18", "2026-10-24", "2026-10-25"]
    assert not list(schedules.occurrences(_schedule(enable=False), monday, week, zone))
    assert not list(schedules.occurrences(_schedule(week_day=0), monday, week, zone))


async def test_a_failed_schedules_read_keeps_the_plans(hass: HomeAssistant, ws: WS, entry: MockConfigEntry) -> None:
    sim = entry.runtime_data.robot.simulator
    sim.cmd_read_schedules = lambda value: sim._feedback("read_schedules", -1, "unsupported", "")  # type: ignore[method-assign]
    assert (await ws("yarbie/plans/save", name="Back only", area_ids=[2]))["success"]
    await hass.async_block_till_done()
    assert "Back only" in state(hass, "select.yarbo_plan").attributes["options"]
    assert state(hass, DEMO).state == "on"  # the last schedules read is kept, not emptied


async def test_switch_refuses_a_schedule_without_a_known_plan(hass: HomeAssistant, entry: MockConfigEntry) -> None:
    c = entry.runtime_data
    c.robot.simulator.schedules[0]["plan_id"] = 99
    await c._refresh_plans()
    with pytest.raises(HomeAssistantError, match="no plan"):
        await hass.services.async_call("switch", "turn_off", {"entity_id": DEMO}, blocking=True)
    assert c.robot.simulator.schedules[0]["enable"] is True


async def test_switches_of_schedules_gone_while_stopped_are_removed(
    hass: HomeAssistant, entry: MockConfigEntry
) -> None:
    registry = er.async_get(hass)
    stale = registry.async_get_or_create(
        "switch", "yarbie", f"{entry.unique_id}_schedule_7", config_entry=entry, suggested_object_id="old_schedule"
    )
    assert await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    assert registry.async_get(stale.entity_id) is None
    assert registry.async_get(DEMO) is not None
