"""Switches: voice prompts, person detection, child lock."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from homeassistant.components.switch import SwitchEntity, SwitchEntityDescription
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from ._vendor.yarbo_local.models import RobotState
from .coordinator import YarbieConfigEntry, YarbieCoordinator
from .entity import YarbieEntity
from .robot import Robot


@dataclass(frozen=True, kw_only=True)
class YarbieSwitchDescription(SwitchEntityDescription):
    value: Callable[[RobotState], bool | None]
    set: Callable[[Robot, bool], Awaitable[Any]]


SWITCHES: tuple[YarbieSwitchDescription, ...] = (
    YarbieSwitchDescription(
        key="sound",
        entity_category=EntityCategory.CONFIG,
        value=lambda s: s.sound_enabled,
        set=lambda r, on: r.set_sound(on),
    ),
    YarbieSwitchDescription(
        key="person_detection",
        entity_category=EntityCategory.CONFIG,
        value=lambda s: s.person_detection,
        set=lambda r, on: r.set_person_detection(on),
    ),
    YarbieSwitchDescription(
        key="child_lock",
        entity_category=EntityCategory.CONFIG,
        value=lambda s: s.child_lock,
        set=lambda r, on: r.set_child_lock(on),
    ),
)


async def async_setup_entry(
    hass: HomeAssistant, entry: YarbieConfigEntry, add: AddConfigEntryEntitiesCallback
) -> None:
    add(YarbieSwitch(entry.runtime_data, d) for d in SWITCHES)


class YarbieSwitch(YarbieEntity, SwitchEntity):
    entity_description: YarbieSwitchDescription

    def __init__(self, coordinator: YarbieCoordinator, description: YarbieSwitchDescription) -> None:
        super().__init__(coordinator, description.key)
        self.entity_description = description

    @property
    def is_on(self) -> bool | None:
        return self.entity_description.value(self.coordinator.robot.state)

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self._set(True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self._set(False)

    async def _set(self, on: bool) -> None:
        robot = self.coordinator.robot
        await self.coordinator.run_control(lambda: self.entity_description.set(robot, on))
