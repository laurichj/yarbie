"""The robot's schedules, as yarbie shows them, and the one reading of them it assumes.

Only a disabled schedule was ever stored on a real robot (3.14.11), so what
``schedule_type``, ``week_day`` and ``return_method`` mean when one fires is not known,
nor whether the times are local or UTC. The robot's numbers are kept and sent as they
are. Only the calendar has to turn them into dates, and it does so under ``READING``,
which says on every event that it is unconfirmed. Captures from the Yarbo app are needed
to settle it (docs/ha-design.md, TODO 4 step 1).
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import date, datetime, time, timedelta, tzinfo
from typing import Any

from ._vendor.yarbo_local.models import Schedule

# What the app sent with its one captured enabled schedule; used for new schedules.
APP_DEFAULTS = {"schedule_type": 3, "week_day": 1, "return_method": 2}

READING = (
    "unconfirmed: week_day read as a bitmask with Monday as 1, Tuesday 2, Wednesday 4 "
    "and so on, times as Home Assistant's local time, an end before the start as the "
    "next day"
)


# The eleven keys the app sends with save_schedule.
_SENT = frozenset(
    {
        "id", "plan_id", "name", "enable", "start_time", "end_time", "week_day",
        "schedule_type", "return_method", "enable_last_progress", "last_progress",
    }
)


def public(schedule: Schedule, plan_names: dict[int, str]) -> dict[str, Any]:
    """A schedule for the card: the robot's fields by their own names, numbers as stored."""
    return {
        "id": schedule.id,
        "name": schedule.name,
        "plan_id": schedule.plan_id,
        "plan": plan_names.get(schedule.plan_id) if schedule.plan_id is not None else None,
        "enabled": schedule.enabled,
        "start_time": schedule.start_time,
        "end_time": schedule.end_time,
        "week_day": schedule.week_day,
        "schedule_type": schedule.schedule_type,
        "return_method": schedule.return_method,
        "resume_progress": schedule.resume_progress,
        "last_progress": schedule.last_progress,
        # What the robot adds to the record (interval_time, times, timezone...).
        "extra": {k: v for k, v in schedule.raw.items() if k not in _SENT},
    }


def fields(schedule: Schedule) -> dict[str, Any]:
    """The schedule as yarbo-local's ``save_schedule`` takes it, to send back changed."""
    return {
        "schedule_id": schedule.id,
        "plan_id": schedule.plan_id,
        "name": schedule.name,
        "start_time": schedule.start_time,
        "end_time": schedule.end_time,
        "week_day": schedule.week_day if schedule.week_day is not None else APP_DEFAULTS["week_day"],
        "schedule_type": (
            schedule.schedule_type
            if schedule.schedule_type is not None
            else APP_DEFAULTS["schedule_type"]
        ),
        "enabled": schedule.enabled,
        "return_method": (
            schedule.return_method
            if schedule.return_method is not None
            else APP_DEFAULTS["return_method"]
        ),
        "resume_progress": schedule.resume_progress,
        "last_progress": schedule.last_progress,
    }


# -- the calendar's reading (unconfirmed, see READING)


def weekdays(week_day: int | None) -> list[int]:
    """Python weekdays (Monday 0) under the assumed bitmask reading."""
    if week_day is None or week_day <= 0:
        return []
    return [day for day in range(7) if week_day & (1 << day)]


def _clock(text: str) -> time | None:
    try:
        return time.fromisoformat(text)
    except ValueError:
        return None


def occurrences(
    schedule: Schedule, start: datetime, end: datetime, zone: tzinfo
) -> Iterator[tuple[datetime, datetime]]:
    """Each (start, end) of an enabled schedule overlapping ``start``..``end``."""
    begins, ends = _clock(schedule.start_time), _clock(schedule.end_time)
    days = weekdays(schedule.week_day)
    if not schedule.enabled or begins is None or ends is None or not days:
        return
    first: date = start.astimezone(zone).date() - timedelta(days=1)
    last: date = end.astimezone(zone).date()
    day = first
    while day <= last:
        if day.weekday() in days:
            opens = datetime.combine(day, begins, zone)
            closes = datetime.combine(day, ends, zone)
            if closes <= opens:
                closes += timedelta(days=1)
            if closes > start and opens < end:
                yield opens, closes
        day += timedelta(days=1)
