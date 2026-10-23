"""Plan lifecycle events: started, paused, resumed, finished (and why)."""

from __future__ import annotations

from homeassistant.components.event import EventEntity
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from ._vendor.yarbo_local.lifecycle import EventKind, LifecycleEvent
from .const import SIGNAL_PLAN_EVENT
from .coordinator import YarbieConfigEntry, YarbieCoordinator
from .entity import YarbieEntity


async def async_setup_entry(
    hass: HomeAssistant, entry: YarbieConfigEntry, add: AddConfigEntryEntitiesCallback
) -> None:
    add([YarbiePlanEvent(entry.runtime_data)])


class YarbiePlanEvent(YarbieEntity, EventEntity):
    _attr_event_types = [k.value for k in EventKind]

    def __init__(self, coordinator: YarbieCoordinator) -> None:
        super().__init__(coordinator, "plan_event")

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        self.async_on_remove(
            async_dispatcher_connect(
                self.hass,
                SIGNAL_PLAN_EVENT.format(self.coordinator.entry.entry_id),
                self._on_event,
            )
        )

    @callback
    def _on_event(self, event: LifecycleEvent) -> None:
        attrs = event.attributes()
        if (name := self.coordinator.plan_name(event.plan_id)) is not None:
            attrs["plan"] = name
        self._trigger_event(event.kind.value, attrs)
        self.async_write_ha_state()
