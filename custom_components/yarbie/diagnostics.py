"""Diagnostics download: the robot's state and map, with serials, addresses and places hidden.

Redaction is yarbo-local's own (the one used for shared fixtures): serials and MACs are
replaced consistently, coordinates are shifted, Wi-Fi names and IPs are removed.
"""

from __future__ import annotations

from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.const import CONF_HOST
from homeassistant.core import HomeAssistant

from ._vendor.yarbo_local.redact import redactor_for_salt
from .const import CONF_SERIAL, OPT_GOOGLE_MAPS_KEY
from .coordinator import YarbieConfigEntry


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: YarbieConfigEntry
) -> dict[str, Any]:
    c = entry.runtime_data
    redactor = redactor_for_salt(None)
    last = c.service.last
    return {
        "entry": {
            "data": async_redact_data(dict(entry.data), {CONF_HOST, CONF_SERIAL}),
            "options": async_redact_data(dict(entry.options), {OPT_GOOGLE_MAPS_KEY}),
        },
        "connected": c.robot.connected,
        "writable": c.writable,
        "firmware": c.robot.state.firmware,
        "verified_firmware": sorted(c.robot.robot.session.registry.verified_firmware),
        "activity": str(c.robot.state.activity),
        "state": redactor.redact_value(dict(c.robot.state.raw)),
        "plans": [{"id": p.id, "name": p.name, "areas": list(p.area_ids)} for p in c.plans],
        "map_revision": last.revision if last else None,
        "map": redactor.redact_value(last.raw) if last else None,
    }
