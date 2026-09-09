from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timedelta
from typing import Protocol, runtime_checkable

from scheduling.models import MisfirePolicy, ScheduleToken


ScheduledCallback = Callable[[], None]


@runtime_checkable
class CalendarSchedule(Protocol):
    """Caller-owned rule for timezone-aware recurring occurrences."""

    def next_after(self, instant: datetime) -> datetime | None:
        """Return the next timezone-aware occurrence strictly after ``instant``."""
        ...


@runtime_checkable
class TemporalScheduler(Protocol):
    """Schedule caller-owned callbacks without assigning them notification semantics."""

    def schedule_at(
        self,
        deadline: datetime,
        callback: ScheduledCallback,
        *,
        misfire_policy: MisfirePolicy = MisfirePolicy.FIRE_ONCE,
    ) -> ScheduleToken:
        """Register a one-shot callback for a timezone-aware wall-clock deadline."""
        ...

    def schedule_after(
        self,
        delay: timedelta,
        callback: ScheduledCallback,
    ) -> ScheduleToken:
        """Register a one-shot callback after a positive monotonic duration."""
        ...

    def schedule_recurring(
        self,
        schedule: CalendarSchedule,
        callback: ScheduledCallback,
        *,
        misfire_policy: MisfirePolicy = MisfirePolicy.FIRE_ONCE,
    ) -> ScheduleToken:
        """Register a callback for each occurrence produced by ``schedule``."""
        ...

    def cancel(self, token: ScheduleToken) -> bool:
        """Cancel a registration, returning whether it was still active."""
        ...


__all__ = ["CalendarSchedule", "ScheduledCallback", "TemporalScheduler"]
