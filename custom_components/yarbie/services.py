"""Actions: start a plan by name or id, set the lights, read the map as GeoJSON."""

from __future__ import annotations

from typing import Any

import probatio

from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import (
    HomeAssistant,
    ServiceCall,
    ServiceResponse,
    SupportsResponse,
    callback,
)
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import config_validation as cv

from .const import DOMAIN
from .coordinator import YarbieCoordinator
from .robot import LIGHT_CHANNELS

ATTR_ENTRY = "config_entry_id"

START_PLAN = probatio.Schema(
    {
        probatio.Optional(ATTR_ENTRY): cv.string,
        probatio.Exclusive("plan", "plan"): cv.string,
        probatio.Exclusive("plan_id", "plan"): probatio.Coerce(int),
    }
)
SET_LIGHTS = probatio.Schema(
    {
        probatio.Optional(ATTR_ENTRY): cv.string,
        probatio.Optional("brightness"): probatio.All(probatio.Coerce(int), probatio.Range(0, 255)),
        **{probatio.Optional(ch): probatio.All(probatio.Coerce(int), probatio.Range(0, 255)) for ch in LIGHT_CHANNELS},
    }
)
GET_MAP = probatio.Schema({probatio.Optional(ATTR_ENTRY): cv.string})


def _coordinator(hass: HomeAssistant, call: ServiceCall) -> YarbieCoordinator:
    wanted = call.data.get(ATTR_ENTRY)
    for entry in hass.config_entries.async_entries(DOMAIN):
        if entry.state is ConfigEntryState.LOADED and wanted in (None, entry.entry_id):
            return entry.runtime_data
    raise ServiceValidationError("no yarbie robot is set up" if wanted is None else f"no robot {wanted}")


async def _start_plan(call: ServiceCall) -> None:
    c = _coordinator(call.hass, call)
    plan_id = call.data.get("plan_id")
    if "plan" in call.data:
        plan_id = next((p.id for p in c.plans if p.name == call.data["plan"]), None)
        if plan_id is None:
            raise ServiceValidationError(f"no plan named {call.data['plan']!r}")
    if plan_id is None:
        plan_id = c.selected_plan_id()
    if plan_id is None:
        raise ServiceValidationError("the robot has no plans")
    await c.async_start_plan(plan_id, call.context)


async def _set_lights(call: ServiceCall) -> None:
    c = _coordinator(call.hass, call)
    base = call.data.get("brightness", 0)
    levels = {ch: call.data.get(ch, base) for ch in LIGHT_CHANNELS}
    await c.run_control(lambda: c.robot.set_lights(levels))


async def _get_map(call: ServiceCall) -> ServiceResponse:
    c = _coordinator(call.hass, call)
    view = await c.service.load()
    public: dict[str, Any] = view.public()
    return {
        "revision": public["revision"],
        "reference": public["reference"],
        "features": public["features"],
    }


@callback
def async_register(hass: HomeAssistant) -> None:
    hass.services.async_register(DOMAIN, "start_plan", _start_plan, schema=START_PLAN)
    hass.services.async_register(DOMAIN, "set_lights", _set_lights, schema=SET_LIGHTS)
    hass.services.async_register(
        DOMAIN, "get_map", _get_map, schema=GET_MAP, supports_response=SupportsResponse.ONLY
    )
