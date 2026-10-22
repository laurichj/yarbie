"""Buttons: beep, return to dock, pause, resume, stop, wake, refresh the map."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from homeassistant.components.button import ButtonEntity, ButtonEntityDescription
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .coordinator import YarbieConfigEntry, YarbieCoordinator
from .entity import YarbieEntity


@dataclass(frozen=True, kw_only=True)
class YarbieButtonDescription(ButtonEntityDescription):
    press: Callable[[YarbieCoordinator], Awaitable[Any]]
    # Reads only: allowed in observe-only mode.
    control: bool = True


BUTTONS: tuple[YarbieButtonDescription, ...] = (
    YarbieButtonDescription(key="beep", press=lambda c: c.robot.beep()),
    YarbieButtonDescription(key="return_to_dock", press=lambda c: c.robot.dock()),
    YarbieButtonDescription(key="pause", press=lambda c: c.robot.pause()),
    YarbieButtonDescription(key="resume", press=lambda c: c.robot.resume()),
    YarbieButtonDescription(key="stop", press=lambda c: c.robot.stop()),
    YarbieButtonDescription(
        key="wake", entity_category=EntityCategory.DIAGNOSTIC, press=lambda c: c.robot.wake()
    ),
    YarbieButtonDescription(
        key="refresh_map",
        entity_category=EntityCategory.DIAGNOSTIC,
        control=False,
        press=lambda c: c.async_load_map(),
    ),
)


async def async_setup_entry(
    hass: HomeAssistant, entry: YarbieConfigEntry, add: AddConfigEntryEntitiesCallback
) -> None:
    add(YarbieButton(entry.runtime_data, d) for d in BUTTONS)


class YarbieButton(YarbieEntity, ButtonEntity):
    entity_description: YarbieButtonDescription

    def __init__(self, coordinator: YarbieCoordinator, description: YarbieButtonDescription) -> None:
        super().__init__(coordinator, description.key)
        self.entity_description = description

    async def async_press(self) -> None:
        press = self.entity_description.press
        if self.entity_description.control:
            await self.coordinator.run_control(lambda: press(self.coordinator))
        else:
            await press(self.coordinator)
