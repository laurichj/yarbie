"""The device and the base every yarbie entity shares."""

from __future__ import annotations

from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN, MANUFACTURER
from .coordinator import YarbieCoordinator


class YarbieEntity(CoordinatorEntity[YarbieCoordinator]):
    _attr_has_entity_name = True

    def __init__(self, coordinator: YarbieCoordinator, key: str) -> None:
        super().__init__(coordinator)
        serial = coordinator.serial
        self._attr_unique_id = f"{serial}_{key}"
        self._attr_translation_key = key
        state = coordinator.robot.state
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, serial)},
            manufacturer=MANUFACTURER,
            model=state.head_name.replace("_", " ").capitalize() if state.head_type else None,
            name="Yarbo",
            serial_number=serial,
            sw_version=state.firmware,
        )

    @property
    def available(self) -> bool:
        return self.coordinator.robot.connected
