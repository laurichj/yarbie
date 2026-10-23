"""Imagery, actions, diagnostics and the firmware repair."""

from __future__ import annotations

import time
from typing import Any

from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker

from custom_components.yarbie.const import CONF_ACCEPTED_FIRMWARE, DOMAIN
from custom_components.yarbie.imagery import GOOGLE, GoogleImagery
from homeassistant.core import HomeAssistant
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.aiohttp_client import async_get_clientsession


def _fake_google(aioclient_mock: AiohttpClientMocker) -> None:
    aioclient_mock.post(
        f"{GOOGLE}/v1/createSession",
        json={"session": "S1", "expiry": str(int(time.time()) + 14 * 86400)},
    )
    aioclient_mock.get(
        f"{GOOGLE}/tile/v1/viewport",
        json={
            "copyright": "Imagery ©2026 Airbus, Maxar Technologies",
            "maxZoomRects": [{"maxZoom": 21}, {"maxZoom": 22}],
        },
    )


def _sessions_opened(aioclient_mock: AiohttpClientMocker) -> int:
    return sum(1 for method, url, *_ in aioclient_mock.mock_calls if url.path == "/v1/createSession")


async def test_session_is_opened_once_and_reused(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    _fake_google(aioclient_mock)
    google = GoogleImagery(hass, "KEY123456", async_get_clientsession(hass))
    source = await google.source((47.87, -121.71))
    assert source["maxzoom"] == 22
    assert "Maxar" in source["attribution"]
    assert "session=S1" in source["tiles"] and "{z}/{x}/{y}" in source["tiles"]
    await GoogleImagery(hass, "KEY123456", async_get_clientsession(hass)).source((47.87, -121.71))
    assert _sessions_opened(aioclient_mock) == 1


async def test_a_new_key_gets_a_new_session(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    _fake_google(aioclient_mock)
    await GoogleImagery(hass, "KEY111111", async_get_clientsession(hass)).source(None)
    await GoogleImagery(hass, "KEY222222", async_get_clientsession(hass)).source(None)
    assert _sessions_opened(aioclient_mock) == 2


async def test_get_map_action(hass: HomeAssistant, entry: MockConfigEntry) -> None:
    response: Any = await hass.services.async_call(DOMAIN, "get_map", {}, blocking=True, return_response=True)
    assert response["revision"] and response["features"]["features"]


async def test_start_plan_by_name(hass: HomeAssistant, entry: MockConfigEntry) -> None:
    sim = entry.runtime_data.robot.simulator
    started: list[Any] = []
    original = sim.cmd_start_plan
    sim.cmd_start_plan = lambda value: (started.append(value), original(value))  # type: ignore[method-assign]
    await hass.services.async_call(DOMAIN, "start_plan", {"plan": "Front only"}, blocking=True)
    assert started[0]["id"] == 2


async def test_diagnostics_hide_the_serial(hass: HomeAssistant, entry: MockConfigEntry) -> None:
    from custom_components.yarbie.diagnostics import async_get_config_entry_diagnostics

    await entry.runtime_data.async_load_map()
    diag = await async_get_config_entry_diagnostics(hass, entry)
    assert diag["entry"]["data"]["serial"] == "**REDACTED**"
    assert diag["map_revision"] and diag["map"]
    assert "SIM-YARBIE-0001" not in str(diag)


async def test_accepting_untested_firmware(hass: HomeAssistant, entry: MockConfigEntry) -> None:
    from custom_components.yarbie.repairs import AcceptFirmwareFlow, async_create_fix_flow

    coordinator = entry.runtime_data
    coordinator.robot.simulator.snapshot["version"] = "9.9.9"
    await coordinator.robot.snapshot()
    coordinator.check_firmware()
    issue_id = f"untested_firmware_{entry.entry_id}"
    issue = ir.async_get(hass).async_get_issue(DOMAIN, issue_id)
    assert issue is not None and not coordinator.writable

    flow = await async_create_fix_flow(hass, issue_id, issue.data)
    assert isinstance(flow, AcceptFirmwareFlow)
    flow.hass = hass
    flow.issue_id = issue_id
    form = await flow.async_step_init()
    assert form["step_id"] == "confirm"
    await flow.async_step_confirm({})
    assert entry.data[CONF_ACCEPTED_FIRMWARE] == "9.9.9"
    assert coordinator.writable
    assert ir.async_get(hass).async_get_issue(DOMAIN, issue_id) is None


def test_translations_match_strings() -> None:
    from pathlib import Path

    root = Path(__file__).parents[2] / "custom_components" / "yarbie"
    assert (root / "translations" / "en.json").read_text() == (root / "strings.json").read_text()


async def test_blueprints_are_valid(hass: HomeAssistant) -> None:
    """Each blueprint parses, and with sample inputs is a valid automation."""
    from pathlib import Path

    from homeassistant.components.automation.config import (
        AUTOMATION_BLUEPRINT_SCHEMA,
        async_validate_config_item,
    )
    from homeassistant.components.blueprint.models import Blueprint, BlueprintInputs
    from homeassistant.util.yaml import load_yaml_dict

    notify = [{"action": "persistent_notification.create", "data": {"message": "hi"}}]
    samples = {
        "rain_pause": {"rain": "binary_sensor.yarbo_rain", "mower": "lawn_mower.yarbo"},
        "low_battery": {
            "battery": "sensor.yarbo_battery",
            "charging": "binary_sensor.yarbo_charging",
            "notify": notify,
        },
        "job_complete": {"plan_event": "event.yarbo_plan", "notify": notify},
    }
    folder = Path(__file__).parents[2] / "blueprints" / "automation" / "yarbie"
    paths = sorted(folder.glob("*.yaml"))
    # Found none would pass silently, as it did when CI tested a copy without blueprints/.
    assert sorted(p.stem for p in paths) == sorted(samples), folder
    for path in paths:
        blueprint = Blueprint(
            load_yaml_dict(str(path)), expected_domain="automation", schema=AUTOMATION_BLUEPRINT_SCHEMA
        )
        inputs = BlueprintInputs(
            blueprint, {"use_blueprint": {"path": path.name, "input": samples[path.stem]}}
        )
        inputs.validate()
        config = inputs.async_substitute()
        config.pop("use_blueprint", None)
        validated = await async_validate_config_item(hass, path.stem, {"id": path.stem, **config})
        assert validated is not None and not getattr(validated, "validation_error", None), path.name
