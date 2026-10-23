"""Switches: voice prompts, person detection, child lock, and one per schedule."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from homeassistant.components.switch import SwitchEntity, SwitchEntityDescription
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import schedules
from ._vendor.yarbo_local.models import RobotState, Schedule
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
    coordinator = entry.runtime_data
    add(YarbieSwitch(coordinator, d) for d in SWITCHES)
    # Schedules come and go (in the card or the app): a switch for each one there is now.
    known: set[int] = set()

    prefix = f"{coordinator.serial}_schedule_"

    @callback
    def sync() -> None:
        current = {s.id for s in coordinator.schedules}
        if new := current - known:
            known.update(new)
            add(YarbieScheduleSwitch(coordinator, i) for i in sorted(new))
        if not coordinator.schedules_read:
            return  # an empty list until the robot has answered; remove nothing
        # Every schedule switch the registry holds for this robot, including ones left
        # from schedules deleted (in the app, say) while Home Assistant was stopped.
        registry = er.async_get(hass)
        wanted = {f"{prefix}{i}" for i in current}
        for entity in er.async_entries_for_config_entry(registry, entry.entry_id):
            if entity.domain == "switch" and entity.unique_id.startswith(prefix) and entity.unique_id not in wanted:
                known.discard(int(entity.unique_id.removeprefix(prefix)))
                registry.async_remove(entity.entity_id)

    sync()
    entry.async_on_unload(coordinator.async_add_listener(sync))


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


class YarbieScheduleSwitch(YarbieEntity, SwitchEntity):
    """A schedule on or off. The rest of the schedule is sent back as the robot holds it."""

    def __init__(self, coordinator: YarbieCoordinator, schedule_id: int) -> None:
        super().__init__(coordinator, f"schedule_{schedule_id}")
        self._attr_translation_key = "schedule"  # one name for all, with the schedule's in it
        self.schedule_id = schedule_id

    @property
    def schedule(self) -> Schedule | None:
        return next((s for s in self.coordinator.schedules if s.id == self.schedule_id), None)

    @property
    def translation_placeholders(self) -> dict[str, str]:
        schedule = self.schedule
        return {"name": (schedule.name if schedule else "") or f"#{self.schedule_id}"}

    @property
    def available(self) -> bool:
        return super().available and self.schedule is not None

    @property
    def is_on(self) -> bool | None:
        schedule = self.schedule
        return schedule.enabled if schedule else None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        schedule = self.schedule
        if schedule is None:
            return {}
        # The robot's numbers as stored: what they mean is not confirmed yet.
        return {
            "plan": self.coordinator.plan_name(schedule.plan_id),
            "start_time": schedule.start_time,
            "end_time": schedule.end_time,
            "week_day": schedule.week_day,
            "schedule_type": schedule.schedule_type,
            "return_method": schedule.return_method,
            "resume_progress": schedule.resume_progress,
            "calendar_reading": schedules.READING,
        }

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self._set(True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self._set(False)

    async def _set(self, on: bool) -> None:
        await self.coordinator.run_write(
            lambda: self.coordinator.async_enable_schedule(self.schedule_id, on)
        )
