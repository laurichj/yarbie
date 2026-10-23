"""The robot's schedules as a calendar, so they show in HA's calendar and automations.

The robot's numbers are turned into dates under ``schedules.READING``, which is not
confirmed yet; every event says so.
"""

from __future__ import annotations

from datetime import datetime, timedelta

from homeassistant.components.calendar import CalendarEntity, CalendarEvent
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.util import dt as dt_util

from . import schedules
from .coordinator import YarbieConfigEntry, YarbieCoordinator
from .entity import YarbieEntity


async def async_setup_entry(
    hass: HomeAssistant, entry: YarbieConfigEntry, add: AddConfigEntryEntitiesCallback
) -> None:
    add([YarbieScheduleCalendar(entry.runtime_data)])


class YarbieScheduleCalendar(YarbieEntity, CalendarEntity):
    def __init__(self, coordinator: YarbieCoordinator) -> None:
        super().__init__(coordinator, "schedules")

    def _events(self, start: datetime, end: datetime) -> list[CalendarEvent]:
        zone = dt_util.get_default_time_zone()
        events = []
        for schedule in self.coordinator.schedules:
            plan = self.coordinator.plan_name(schedule.plan_id) or f"plan {schedule.plan_id}"
            description = (
                f"Runs {plan}. When, as yarbie reads it ({schedules.READING}): week_day "
                f"{schedule.week_day}, {schedule.start_time} to {schedule.end_time}. "
                f"schedule_type {schedule.schedule_type} and return_method "
                f"{schedule.return_method} are kept as the robot holds them; their meanings "
                "are not known yet."
            )
            for opens, closes in schedules.occurrences(schedule, start, end, zone):
                events.append(
                    CalendarEvent(
                        start=opens,
                        end=closes,
                        summary=schedule.name or plan,
                        description=description,
                        uid=f"{self.coordinator.serial}-schedule-{schedule.id}-{opens.isoformat()}",
                    )
                )
        return sorted(events, key=lambda e: e.start_datetime_local)

    @property
    def event(self) -> CalendarEvent | None:
        """The run on now, or the next one within a week."""
        now = dt_util.now()
        return next(iter(self._events(now, now + timedelta(days=8))), None)

    async def async_get_events(
        self, hass: HomeAssistant, start_date: datetime, end_date: datetime
    ) -> list[CalendarEvent]:
        return self._events(start_date, end_date)
