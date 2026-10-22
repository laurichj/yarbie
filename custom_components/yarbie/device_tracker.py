"""Where the robot is: its map position turned to latitude and longitude."""

from __future__ import annotations

from homeassistant.components.device_tracker import TrackerEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .coordinator import YarbieConfigEntry, YarbieCoordinator
from .entity import YarbieEntity


async def async_setup_entry(
    hass: HomeAssistant, entry: YarbieConfigEntry, add: AddConfigEntryEntitiesCallback
) -> None:
    add([YarbieTracker(entry.runtime_data)])


class YarbieTracker(YarbieEntity, TrackerEntity):
    def __init__(self, coordinator: YarbieCoordinator) -> None:
        super().__init__(coordinator, "position")

    @property
    def latitude(self) -> float | None:
        position = self.coordinator.position_wgs84()
        # 1e-6 degrees is about 0.1 m: finer only adds recorder rows.
        return round(position[0], 6) if position else None

    @property
    def longitude(self) -> float | None:
        position = self.coordinator.position_wgs84()
        return round(position[1], 6) if position else None
