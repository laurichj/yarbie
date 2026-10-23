"""Plans for the dashboard's plan card: the areas it lists, and the write rails."""

from __future__ import annotations

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.yarbie.const import OPT_ALLOW_WRITES
from homeassistant.core import HomeAssistant

from conftest import WS, busy


async def test_areas_list(ws: WS) -> None:
    areas = (await ws("yarbie/areas/list"))["result"]
    assert [(a["id"], a["name"]) for a in areas] == [(1, "Front Lawn"), (2, "Back Lawn")]
    assert all(a["algorithm_type"] == 0 for a in areas)


async def test_plan_keeps_the_area_order(ws: WS) -> None:
    created = (await ws("yarbie/plans/save", name="Back first", area_ids=[2, 1]))["result"]
    plans = (await ws("yarbie/plans/list"))["result"]
    assert next(p for p in plans if p["id"] == created["id"])["area_ids"] == [2, 1]
    deleted = await ws("yarbie/plans/delete", plan_id=created["id"])
    assert deleted["success"]
    assert all(p["id"] != created["id"] for p in (await ws("yarbie/plans/list"))["result"])


async def test_plan_writes_wait_for_the_robot_to_stop(
    hass: HomeAssistant, ws: WS, entry: MockConfigEntry
) -> None:
    await busy(hass, entry)
    saved = await ws("yarbie/plans/save", name="During a run", area_ids=[1])
    assert saved["error"]["code"] == "conflict"
    deleted = await ws("yarbie/plans/delete", plan_id=1)
    assert deleted["error"]["code"] == "conflict"
    assert len((await ws("yarbie/plans/list"))["result"]) == 2


@pytest.mark.parametrize("options", [{OPT_ALLOW_WRITES: False}])
async def test_plan_writes_obey_the_kill_switch(ws: WS) -> None:
    saved = await ws("yarbie/plans/save", name="Nope", area_ids=[1])
    assert saved["error"]["code"] == "read_only"
