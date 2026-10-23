"""Sensors: what the robot is doing, its battery, run progress and diagnostics."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.const import (
    DEGREE,
    PERCENTAGE,
    REVOLUTIONS_PER_MINUTE,
    EntityCategory,
    UnitOfElectricCurrent,
    UnitOfElectricPotential,
    UnitOfSpeed,
    UnitOfTime,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from ._vendor.yarbo_local.models import Activity
from .coordinator import YarbieConfigEntry, YarbieCoordinator
from .entity import YarbieEntity

HEADS = ["none", "snow_blower", "leaf_blower", "mower", "smart_cover", "mower_pro", "trimmer", "unknown"]


def _float(value: Any, scale: float = 1.0) -> float | None:
    return round(float(value) * scale, 3) if isinstance(value, int | float) else None


def _fault(c: YarbieCoordinator) -> str:
    fault = c.robot.state.fault
    if fault is None:
        return "none"
    return fault.key or f"code {fault.code}"


def _fault_attrs(c: YarbieCoordinator) -> dict[str, Any]:
    fault = c.robot.state.fault
    if fault is None:
        return {}
    return {"code": fault.code, "description": fault.description, "hint": fault.hint}


def _progress(c: YarbieCoordinator) -> float | None:
    fb = c.feedback
    return fb.progress if fb is not None and c.robot.state.plan_running else None


def _remaining(c: YarbieCoordinator) -> float | None:
    fb = c.feedback
    if fb is None or not c.robot.state.plan_running or fb.remaining_s is None:
        return None
    return round(fb.remaining_s / 60, 1)


def _plan(c: YarbieCoordinator) -> str | None:
    run = c.tracker.current
    if run is None:
        return None
    return c.plan_name(run.plan_id) or (str(run.plan_id) if run.plan_id is not None else None)


OUTCOMES = ["completed", "partial", "returned_to_dock", "stopped", "superseded", "unknown"]


def _last(key: str) -> Callable[[YarbieCoordinator], Any]:
    return lambda c: c.last_run.get(key) if c.last_run else None


def _last_outcome(c: YarbieCoordinator) -> str | None:
    if not c.last_run:
        return None
    outcome = c.last_run.get("outcome")
    return outcome if outcome in OUTCOMES else "unknown"


def _last_ended(c: YarbieCoordinator) -> datetime | None:
    ended = _last("ended")(c) or _last("started")(c)
    return datetime.fromtimestamp(ended, UTC) if isinstance(ended, int | float) else None


def _last_minutes(c: YarbieCoordinator) -> float | None:
    work = _last("work_s")(c)
    return round(work / 60, 1) if isinstance(work, int | float) else None


def _last_attrs(c: YarbieCoordinator) -> dict[str, Any]:
    run = c.last_run
    if not run:
        return {}
    keys = ("plan", "started_by", "charge_trips", "battery_used", "mowed_m2", "rain", "faults")
    return {k: run[k] for k in keys if run.get(k) is not None}


@dataclass(frozen=True, kw_only=True)
class YarbieSensorDescription(SensorEntityDescription):
    value: Callable[[YarbieCoordinator], Any]
    attrs: Callable[[YarbieCoordinator], dict[str, Any]] | None = None


def _path(path: str, scale: float = 1.0) -> Callable[[YarbieCoordinator], float | None]:
    return lambda c: _float(c.robot.state.get(path), scale)


SENSORS: tuple[YarbieSensorDescription, ...] = (
    YarbieSensorDescription(
        key="activity",
        device_class=SensorDeviceClass.ENUM,
        options=[a.value for a in Activity],
        value=lambda c: c.robot.state.activity.value,
    ),
    YarbieSensorDescription(
        key="battery",
        device_class=SensorDeviceClass.BATTERY,
        native_unit_of_measurement=PERCENTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        value=lambda c: c.robot.state.battery,
    ),
    YarbieSensorDescription(key="error", value=_fault, attrs=_fault_attrs),
    YarbieSensorDescription(key="plan", value=_plan),
    YarbieSensorDescription(
        key="plan_progress",
        native_unit_of_measurement=PERCENTAGE,
        value=_progress,
    ),
    YarbieSensorDescription(
        key="time_remaining",
        device_class=SensorDeviceClass.DURATION,
        native_unit_of_measurement=UnitOfTime.MINUTES,
        value=_remaining,
    ),
    YarbieSensorDescription(
        key="last_run",
        device_class=SensorDeviceClass.ENUM,
        options=OUTCOMES,
        value=_last_outcome,
        attrs=_last_attrs,
    ),
    YarbieSensorDescription(
        key="last_run_ended",
        device_class=SensorDeviceClass.TIMESTAMP,
        value=_last_ended,
    ),
    YarbieSensorDescription(
        key="last_run_duration",
        device_class=SensorDeviceClass.DURATION,
        native_unit_of_measurement=UnitOfTime.MINUTES,
        value=_last_minutes,
    ),
    YarbieSensorDescription(
        key="last_run_progress",
        native_unit_of_measurement=PERCENTAGE,
        value=_last("progress"),
    ),
    YarbieSensorDescription(
        key="head",
        device_class=SensorDeviceClass.ENUM,
        options=HEADS,
        value=lambda c: c.robot.state.head_name if c.robot.state.head_name in HEADS else "unknown",
    ),
    YarbieSensorDescription(
        key="rtk_status",
        entity_category=EntityCategory.DIAGNOSTIC,
        value=lambda c: c.robot.state.rtk_status,
    ),
    YarbieSensorDescription(
        key="satellites",
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        value=lambda c: c.robot.state.satellites,
    ),
    YarbieSensorDescription(
        key="heading",
        native_unit_of_measurement=DEGREE,
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        value=lambda c: c.robot.state.heading_degrees,
    ),
    YarbieSensorDescription(
        key="battery_voltage",
        device_class=SensorDeviceClass.VOLTAGE,
        native_unit_of_measurement=UnitOfElectricPotential.VOLT,
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        value=lambda c: c.robot.state.battery_voltage,
    ),
    YarbieSensorDescription(
        key="battery_current",
        device_class=SensorDeviceClass.CURRENT,
        native_unit_of_measurement=UnitOfElectricCurrent.AMPERE,
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        value=lambda c: c.robot.state.battery_current,
    ),
    YarbieSensorDescription(
        key="left_blade_rpm",
        native_unit_of_measurement=REVOLUTIONS_PER_MINUTE,
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        value=_path("mower_head_info03.left_blade_motor_rpm"),
    ),
    YarbieSensorDescription(
        key="right_blade_rpm",
        native_unit_of_measurement=REVOLUTIONS_PER_MINUTE,
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        value=_path("mower_head_info04.right_blade_motor_rpm"),
    ),
    YarbieSensorDescription(
        key="left_wheel_speed",
        device_class=SensorDeviceClass.SPEED,
        native_unit_of_measurement=UnitOfSpeed.METERS_PER_SECOND,
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        value=_path("WheelSpeedMSG.left"),
    ),
    YarbieSensorDescription(
        key="right_wheel_speed",
        device_class=SensorDeviceClass.SPEED,
        native_unit_of_measurement=UnitOfSpeed.METERS_PER_SECOND,
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        value=_path("WheelSpeedMSG.right"),
    ),
)


async def async_setup_entry(
    hass: HomeAssistant, entry: YarbieConfigEntry, add: AddConfigEntryEntitiesCallback
) -> None:
    add(YarbieSensor(entry.runtime_data, d) for d in SENSORS)


class YarbieSensor(YarbieEntity, SensorEntity):
    entity_description: YarbieSensorDescription

    def __init__(self, coordinator: YarbieCoordinator, description: YarbieSensorDescription) -> None:
        super().__init__(coordinator, description.key)
        self.entity_description = description

    @property
    def native_value(self) -> Any:
        return self.entity_description.value(self.coordinator)

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        attrs = self.entity_description.attrs
        return attrs(self.coordinator) if attrs else None
