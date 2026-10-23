"""Turning writes back on after checking untested firmware by hand."""

from __future__ import annotations

from typing import Any

import probatio

from homeassistant.components.repairs import RepairsFlow, RepairsFlowResult
from homeassistant.core import HomeAssistant

from .const import CONF_ACCEPTED_FIRMWARE


class AcceptFirmwareFlow(RepairsFlow):
    def __init__(self, entry_id: str, firmware: str, tested: str) -> None:
        self._entry_id = entry_id
        self._firmware = firmware
        self._tested = tested

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> RepairsFlowResult:
        return await self.async_step_confirm()

    async def async_step_confirm(self, user_input: dict[str, Any] | None = None) -> RepairsFlowResult:
        if user_input is not None:
            entry = self.hass.config_entries.async_get_entry(self._entry_id)
            if entry is not None:
                self.hass.config_entries.async_update_entry(
                    entry, data={**entry.data, CONF_ACCEPTED_FIRMWARE: self._firmware}
                )
                entry.runtime_data.check_firmware()
            return self.async_create_entry(data={})
        return self.async_show_form(
            step_id="confirm",
            data_schema=probatio.Schema({}),
            description_placeholders={"firmware": self._firmware, "tested": self._tested},
        )


async def async_create_fix_flow(
    hass: HomeAssistant, issue_id: str, data: dict[str, Any] | None
) -> RepairsFlow:
    assert data is not None
    return AcceptFirmwareFlow(str(data["entry_id"]), str(data["firmware"]), str(data.get("tested", "")))
