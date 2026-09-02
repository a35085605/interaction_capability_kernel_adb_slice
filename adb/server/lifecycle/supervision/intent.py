from __future__ import annotations

from dataclasses import dataclass
from math import isfinite
from numbers import Real


@dataclass(frozen=True, slots=True)
class AdbServerAcquireOnceIntent:
    """Request exactly one ADB server backend acquisition attempt after ``delay_seconds``."""

    attempt_number: int
    delay_seconds: float = 0.0

    def __post_init__(self) -> None:
        if isinstance(self.attempt_number, bool) or not isinstance(self.attempt_number, int):
            raise TypeError("attempt_number must be an integer")
        if self.attempt_number <= 0:
            raise ValueError("attempt_number must be greater than zero")
        if isinstance(self.delay_seconds, bool) or not isinstance(self.delay_seconds, Real):
            raise TypeError("delay_seconds must be a real number")
        delay = float(self.delay_seconds)
        if not isfinite(delay) or delay < 0.0:
            raise ValueError("delay_seconds must be finite and greater than or equal to zero")
        object.__setattr__(self, "delay_seconds", delay)


__all__ = ["AdbServerAcquireOnceIntent"]
