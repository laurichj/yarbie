"""The robot as a lawn mower: start the selected plan, pause, dock."""

from __future__ import annotations

from homeassistant.components.lawn_mower import (
    LawnMowerActivity,
    LawnMowerEntity,
    LawnMowerEntityFeature,
)
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from ._vendor.yarbo_local.models import Activity
from .coordinator import YarbieConfigEntry, YarbieCoordinator
from .entity import YarbieEntity

ACTIVITY = {
    Activity.CALCULATING_ROUTE: LawnMowerActivity.MOWING,
    Activity.HEADING_TO_AREA: LawnMowerActivity.MOWING,
    Activity.WORKING: LawnMowerActivity.MOWING,
    Activity.WAYPOINT: LawnMowerActivity.MOWING,
    Activity.WAITING: LawnMowerActivity.PAUSED,
    Activity.PAUSED: LawnMowerActivity.PAUSED,
    Activity.RETURNING: LawnMowerActivity.RETURNING,
    Activity.CHARGING: LawnMowerActivity.DOCKED,
    Activity.COMPLETED: LawnMowerActivity.DOCKED,
    Activity.IDLE: LawnMowerActivity.DOCKED,
    Activity.SLEEPING: LawnMowerActivity.DOCKED,
    Activity.ERROR: LawnMowerActivity.ERROR,
}


async def async_setup_entry(
    hass: HomeAssistant, entry: YarbieConfigEntry, add: AddConfigEntryEntitiesCallback
) -> None:
    add([YarbieMower(entry.runtime_data)])


class YarbieMower(YarbieEntity, LawnMowerEntity):
    _attr_name = None  # the device's own name
    _attr_supported_features = (
        LawnMowerEntityFeature.START_MOWING
        | LawnMowerEntityFeature.PAUSE
        | LawnMowerEntityFeature.DOCK
    )

    def __init__(self, coordinator: YarbieCoordinator) -> None:
        super().__init__(coordinator, "mower")

    @property
    def activity(self) -> LawnMowerActivity | None:
        return ACTIVITY.get(self.coordinator.robot.state.activity)

    async def async_start_mowing(self) -> None:
        """Resume a paused plan, else start the plan chosen in the plan select."""
        robot = self.coordinator.robot
        if robot.state.activity is Activity.PAUSED:
            await self.coordinator.run_control(robot.resume)
            return
        plan_id = self.coordinator.selected_plan_id()
        if plan_id is None:
            raise HomeAssistantError("the robot has no plans; make one in the Yard editor")
        await self.coordinator.async_start_plan(plan_id, self._context)

    async def async_pause(self) -> None:
        await self.coordinator.run_control(self.coordinator.robot.pause)

    async def async_dock(self) -> None:
        await self.coordinator.run_control(self.coordinator.robot.dock)
