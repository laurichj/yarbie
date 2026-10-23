"""The robot's lights: all seven channels as one light (``light_ctrl`` sets them together)."""

from __future__ import annotations

from typing import Any

from homeassistant.components.light import ATTR_BRIGHTNESS, ColorMode, LightEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .coordinator import YarbieConfigEntry, YarbieCoordinator
from .entity import YarbieEntity
from .robot import LIGHT_CHANNELS


async def async_setup_entry(
    hass: HomeAssistant, entry: YarbieConfigEntry, add: AddConfigEntryEntitiesCallback
) -> None:
    add([YarbieLights(entry.runtime_data)])


class YarbieLights(YarbieEntity, LightEntity):
    _attr_color_mode = ColorMode.BRIGHTNESS
    _attr_supported_color_modes = {ColorMode.BRIGHTNESS}

    def __init__(self, coordinator: YarbieCoordinator) -> None:
        super().__init__(coordinator, "lights")

    def _levels(self) -> list[int]:
        out = []
        for channel in LIGHT_CHANNELS:
            value = self.coordinator.robot.state.get(f"LedInfoMSG.{channel}")
            if isinstance(value, int | float):
                out.append(int(value))
        return out

    @property
    def is_on(self) -> bool | None:
        levels = self._levels()
        return any(levels) if levels else None

    @property
    def brightness(self) -> int | None:
        levels = self._levels()
        return max(levels) if levels else None

    async def async_turn_on(self, **kwargs: Any) -> None:
        level = int(kwargs.get(ATTR_BRIGHTNESS, 255))
        await self._set({ch: level for ch in LIGHT_CHANNELS})

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self._set({})

    async def _set(self, levels: dict[str, int]) -> None:
        robot = self.coordinator.robot
        await self.coordinator.run_control(lambda: robot.set_lights(levels))
