from __future__ import annotations

from datetime import datetime, timedelta
from typing import Protocol, TypeVar, runtime_checkable

from scheduling.models import MisfirePolicy, ScheduleToken


ScheduledEventT = TypeVar("ScheduledEventT", contravariant=True)


@runtime_checkable
class CalendarSchedule(Protocol):
    """Caller-owned rule for timezone-aware recurring occurrences."""

    def next_after(self, instant: datetime) -> datetime | None:
        """Return the next timezone-aware occurrence strictly after ``instant``."""
        ...


@runtime_checkable
class TemporalScheduler(Protocol[ScheduledEventT]):
    """Register data events for non-polling temporal delivery.

    Implementations wait efficiently and deliver events through configured
    orchestration or event-queue infrastructure. They must not invoke domain
    control effects directly.
    """

    def schedule_at(
        self,
        deadline: datetime,
        event: ScheduledEventT,
        *,
        misfire_policy: MisfirePolicy = MisfirePolicy.FIRE_ONCE,
    ) -> ScheduleToken:
        """Register a one-shot event for a timezone-aware wall-clock deadline."""
        ...

    def schedule_after(
        self,
        delay: timedelta,
        event: ScheduledEventT,
    ) -> ScheduleToken:
        """Register a one-shot event after a positive monotonic duration."""
        ...

    def schedule_recurring(
        self,
        schedule: CalendarSchedule,
        event: ScheduledEventT,
        *,
        misfire_policy: MisfirePolicy = MisfirePolicy.FIRE_ONCE,
    ) -> ScheduleToken:
        """Register an event for each occurrence produced by ``schedule``."""
        ...

    def cancel(self, token: ScheduleToken) -> bool:
        """Cancel a registration, returning whether it was still active."""
        ...


__all__ = ["CalendarSchedule", "TemporalScheduler"]
