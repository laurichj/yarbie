"""Setting up against the simulator: the device, its entities, controls and the rails."""

from __future__ import annotations

from typing import Any

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.yarbie.const import OPT_OBSERVE_ONLY
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import device_registry as dr, entity_registry as er

from conftest import state


async def test_setup_creates_the_device_and_entities(hass: HomeAssistant, entry: MockConfigEntry) -> None:
    assert entry.state is ConfigEntryState.LOADED
    devices = dr.async_entries_for_config_entry(dr.async_get(hass), entry.entry_id)
    assert [(d.serial_number, d.sw_version) for d in devices] == [("SIM-YARBIE-0001", "3.14.11")]
    assert state(hass, "sensor.yarbo_battery").state == "87"
    assert state(hass, "sensor.yarbo_head").state == "mower_pro"
    assert state(hass, "lawn_mower.yarbo").state == "docked"
    assert state(hass, "select.yarbo_plan").attributes["options"] == ["Whole yard", "Front only"]
    assert state(hass, "binary_sensor.yarbo_problem").state == "off"
    # Diagnostic streams stay off unless asked for.
    entity = er.async_get(hass).async_get("sensor.yarbo_left_blade_speed")
    assert entity is not None and entity.disabled


async def test_position_is_on_the_map(hass: HomeAssistant, entry: MockConfigEntry) -> None:
    coordinator = entry.runtime_data
    await coordinator.async_load_map()
    await hass.async_block_till_done()
    tracker = state(hass, "device_tracker.yarbo_position")
    assert tracker.attributes["latitude"] == pytest.approx(37.42752, abs=1e-4)


async def test_start_mowing_starts_the_selected_plan(hass: HomeAssistant, entry: MockConfigEntry) -> None:
    await hass.services.async_call(
        "select", "select_option", {"entity_id": "select.yarbo_plan", "option": "Front only"}, blocking=True
    )
    sim = entry.runtime_data.robot.simulator
    assert sim is not None
    started: list[dict[str, Any]] = []
    original = sim.cmd_start_plan

    def record(value: dict[str, Any]) -> None:
        started.append(value)
        original(value)

    sim.cmd_start_plan = record  # type: ignore[method-assign]
    await hass.services.async_call("lawn_mower", "start_mowing", {"entity_id": "lawn_mower.yarbo"}, blocking=True)
    assert started and started[0]["id"] == 2


@pytest.mark.parametrize("options", [{OPT_OBSERVE_ONLY: True}])
async def test_observe_only_sends_nothing(hass: HomeAssistant, entry: MockConfigEntry) -> None:
    with pytest.raises(HomeAssistantError, match="observe only"):
        await hass.services.async_call("button", "press", {"entity_id": "button.yarbo_beep"}, blocking=True)
    # Reads still work.
    await hass.services.async_call("button", "press", {"entity_id": "button.yarbo_refresh_map"}, blocking=True)


async def test_untested_firmware_turns_writes_off(hass: HomeAssistant, entry: MockConfigEntry) -> None:
    from homeassistant.helpers import issue_registry as ir

    coordinator = entry.runtime_data
    assert coordinator.writable
    sim = coordinator.robot.simulator
    sim.snapshot["version"] = "9.9.9"
    await coordinator.robot.snapshot()
    coordinator.check_firmware()
    assert not coordinator.writable
    issue = ir.async_get(hass).async_get_issue("yarbie", f"untested_firmware_{entry.entry_id}")
    assert issue is not None and issue.translation_placeholders["firmware"] == "9.9.9"


async def test_unload(hass: HomeAssistant, entry: MockConfigEntry) -> None:
    assert await hass.config_entries.async_unload(entry.entry_id)
    assert entry.state is ConfigEntryState.NOT_LOADED


async def test_map_image(hass: HomeAssistant, entry: MockConfigEntry, hass_client: Any) -> None:
    await entry.runtime_data.async_load_map()
    await hass.async_block_till_done()
    image = state(hass, "image.yarbo_map")
    client = await hass_client()
    response = await client.get(image.attributes["entity_picture"])
    assert response.status == 200 and response.content_type == "image/svg+xml"
    body = await response.text()
    assert body.startswith("<svg") and "Flower bed" in body
