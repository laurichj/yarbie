"""The plan the lawn mower entity starts."""

from __future__ import annotations

from homeassistant.components.select import SelectEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.restore_state import RestoreEntity

from .coordinator import YarbieConfigEntry, YarbieCoordinator
from .entity import YarbieEntity


async def async_setup_entry(
    hass: HomeAssistant, entry: YarbieConfigEntry, add: AddConfigEntryEntitiesCallback
) -> None:
    add([YarbiePlanSelect(entry.runtime_data)])


class YarbiePlanSelect(YarbieEntity, SelectEntity, RestoreEntity):
    def __init__(self, coordinator: YarbieCoordinator) -> None:
        super().__init__(coordinator, "plan_select")

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        if (last := await self.async_get_last_state()) is not None:
            self.coordinator.selected_plan = last.state

    @property
    def options(self) -> list[str]:
        return [p.name for p in self.coordinator.plans]

    @property
    def current_option(self) -> str | None:
        names = self.options
        chosen = self.coordinator.selected_plan
        if chosen in names:
            return chosen
        return names[0] if names else None

    async def async_select_option(self, option: str) -> None:
        self.coordinator.selected_plan = option
        self.async_write_ha_state()
