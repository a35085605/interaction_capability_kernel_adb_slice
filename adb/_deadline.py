from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field


Clock = Callable[[], float]


@dataclass(frozen=True, slots=True)
class Deadline:
    """Small clock-injected deadline value used for remaining-time calculations."""

    expires_at: float
    _clock: Clock = field(repr=False, compare=False)

    @classmethod
    def after(cls, seconds: float, clock: Clock) -> "Deadline":
        if not callable(clock):
            raise TypeError("clock must be callable")
        return cls(clock() + seconds, clock)

    @classmethod
    def at(cls, expires_at: float, clock: Clock) -> "Deadline":
        if not callable(clock):
            raise TypeError("clock must be callable")
        return cls(expires_at, clock)

    def remaining(self) -> float:
        """Return signed seconds until the deadline."""

        return self.expires_at - self._clock()

    def expired(self) -> bool:
        """Whether the deadline has elapsed."""

        return self.remaining() <= 0.0

    def clamp(self, interval_seconds: float) -> float:
        """Clamp a non-negative wait interval to the remaining deadline budget."""

        if interval_seconds < 0.0:
            raise ValueError("interval_seconds must be non-negative")
        return min(interval_seconds, max(0.0, self.remaining()))


__all__ = ["Deadline"]
