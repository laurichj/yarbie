"""The map as an image entity for dashboards (drawn here, no imagery or network)."""

from __future__ import annotations

from datetime import datetime

from homeassistant.components.image import ImageEntity
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.util import dt as dt_util

from .coordinator import YarbieConfigEntry, YarbieCoordinator
from .editor import render
from .entity import YarbieEntity


async def async_setup_entry(
    hass: HomeAssistant, entry: YarbieConfigEntry, add: AddConfigEntryEntitiesCallback
) -> None:
    add([YarbieMapImage(hass, entry.runtime_data)])


class YarbieMapImage(YarbieEntity, ImageEntity):
    _attr_content_type = "image/svg+xml"

    def __init__(self, hass: HomeAssistant, coordinator: YarbieCoordinator) -> None:
        YarbieEntity.__init__(self, coordinator, "map")
        ImageEntity.__init__(self, hass)
        self._drawn: tuple[str | None, tuple[int, int] | None, int] | None = None

    def _key(self) -> tuple[str | None, tuple[int, int] | None, int]:
        """What the picture shows: the map, the robot to the metre, the run's progress."""
        c = self.coordinator
        last = c.service.last
        position = c.robot.state.position
        spot = (round(position[0]), round(position[1])) if position else None
        done = sum(len(a.done) for a in c.feedback.areas) if c.feedback else 0
        return (last.revision if last else None, spot, done // 20)

    @callback
    def _handle_coordinator_update(self) -> None:
        key = self._key()
        if key != self._drawn:
            self._drawn = key
            self._attr_image_last_updated = dt_util.utcnow()
        super()._handle_coordinator_update()

    @property
    def image_last_updated(self) -> datetime | None:
        return self._attr_image_last_updated

    async def async_image(self) -> bytes | None:
        c = self.coordinator
        if c.service.last is None:
            return None
        mowed = [a.done for a in c.feedback.areas] if c.feedback and c.robot.state.plan_running else []
        return render.svg(c.service.last.raw, c.robot.state.position, mowed)
