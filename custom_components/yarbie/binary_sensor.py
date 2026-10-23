"""Binary sensors: charging, problems, rain, the stop button, RTK."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
    BinarySensorEntityDescription,
)
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from ._vendor.yarbo_local.models import RobotState
from .coordinator import YarbieConfigEntry, YarbieCoordinator
from .entity import YarbieEntity


def _flag(path: str) -> Callable[[RobotState], bool | None]:
    def value(state: RobotState) -> bool | None:
        raw = state.get(path)
        return bool(raw) if isinstance(raw, int | float | bool) else None

    return value


@dataclass(frozen=True, kw_only=True)
class YarbieBinaryDescription(BinarySensorEntityDescription):
    value: Callable[[RobotState], bool | None]


BINARY_SENSORS: tuple[YarbieBinaryDescription, ...] = (
    YarbieBinaryDescription(
        key="charging",
        device_class=BinarySensorDeviceClass.BATTERY_CHARGING,
        value=lambda s: s.charging,
    ),
    YarbieBinaryDescription(
        key="problem",
        device_class=BinarySensorDeviceClass.PROBLEM,
        value=lambda s: s.error_code != 0 or s.plan_error is not None,
    ),
    YarbieBinaryDescription(
        # Candidate field (vendor SDK): 0 dry. Feeds the rain-pause blueprint.
        key="rain",
        device_class=BinarySensorDeviceClass.MOISTURE,
        value=_flag("mower_head_info01.rain_sensor"),
    ),
    YarbieBinaryDescription(
        key="emergency_stop",
        device_class=BinarySensorDeviceClass.PROBLEM,
        value=lambda s: s.stop_button_engaged,
    ),
    YarbieBinaryDescription(
        key="rtk_fix",
        entity_category=EntityCategory.DIAGNOSTIC,
        value=lambda s: s.rtk_usable,
    ),
    YarbieBinaryDescription(
        key="planning",
        device_class=BinarySensorDeviceClass.RUNNING,
        value=lambda s: s.plan_running,
    ),
    YarbieBinaryDescription(
        key="awake",
        entity_category=EntityCategory.DIAGNOSTIC,
        value=lambda s: s.awake,
    ),
    YarbieBinaryDescription(
        key="power_fault",
        device_class=BinarySensorDeviceClass.PROBLEM,
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        value=_flag("BodyMsg.power_fault_state"),
    ),
    YarbieBinaryDescription(
        key="push_pod_fault",
        device_class=BinarySensorDeviceClass.PROBLEM,
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        value=_flag("BodyMsg.push_pod_fault_state"),
    ),
    YarbieBinaryDescription(
        key="left_wheel_fault",
        device_class=BinarySensorDeviceClass.PROBLEM,
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        value=_flag("BodyMsg.left_wheel_fault_state"),
    ),
    YarbieBinaryDescription(
        key="right_wheel_fault",
        device_class=BinarySensorDeviceClass.PROBLEM,
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        value=_flag("BodyMsg.right_wheel_fault_state"),
    ),
)


async def async_setup_entry(
    hass: HomeAssistant, entry: YarbieConfigEntry, add: AddConfigEntryEntitiesCallback
) -> None:
    add(YarbieBinarySensor(entry.runtime_data, d) for d in BINARY_SENSORS)


class YarbieBinarySensor(YarbieEntity, BinarySensorEntity):
    entity_description: YarbieBinaryDescription

    def __init__(self, coordinator: YarbieCoordinator, description: YarbieBinaryDescription) -> None:
        super().__init__(coordinator, description.key)
        self.entity_description = description

    @property
    def is_on(self) -> bool | None:
        return self.entity_description.value(self.coordinator.robot.state)
