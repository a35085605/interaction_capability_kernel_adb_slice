from __future__ import annotations

import math
from dataclasses import dataclass
from numbers import Real

from adb._recovery import normalize_recovery_retry_configuration


@dataclass(frozen=True, slots=True)
class AdbTransportListWatchReleaseSupervisionPolicy:
    """Retry timing for retryable transport-list watch release results."""

    retry_seconds: float = 0.1

    def __post_init__(self) -> None:
        value = self.retry_seconds
        if isinstance(value, bool) or not isinstance(value, Real):
            raise TypeError("watch release supervision retry must be a real number")
        normalized = float(value)
        if not math.isfinite(normalized) or normalized <= 0.0:
            raise ValueError(
                "watch release supervision retry must be finite and greater than zero"
            )
        object.__setattr__(self, "retry_seconds", normalized)


@dataclass(frozen=True, slots=True)
class AdbTransportListWatchRecoveryPolicy:
    """Retry and backoff policy for one transport-list watch recovery cycle."""

    retry_initial_seconds: float = 0.5
    retry_max_seconds: float = 30.0
    retry_multiplier: float = 2.0
    retry_jitter_ratio: float = 0.2
    deferred_retry_seconds: float = 0.1
    max_attempts: int | None = None

    def __post_init__(self) -> None:
        configuration = normalize_recovery_retry_configuration(
            retry_initial_seconds=self.retry_initial_seconds,
            retry_max_seconds=self.retry_max_seconds,
            retry_multiplier=self.retry_multiplier,
            retry_jitter_ratio=self.retry_jitter_ratio,
            deferred_retry_seconds=self.deferred_retry_seconds,
            max_attempts=self.max_attempts,
            subject="ADB transport-list watch recovery",
        )
        object.__setattr__(
            self,
            "retry_initial_seconds",
            configuration.retry_initial_seconds,
        )
        object.__setattr__(self, "retry_max_seconds", configuration.retry_max_seconds)
        object.__setattr__(self, "retry_multiplier", configuration.retry_multiplier)
        object.__setattr__(self, "retry_jitter_ratio", configuration.retry_jitter_ratio)
        object.__setattr__(
            self,
            "deferred_retry_seconds",
            configuration.deferred_retry_seconds,
        )
        object.__setattr__(self, "max_attempts", configuration.max_attempts)


__all__ = [
    "AdbTransportListWatchRecoveryPolicy",
    "AdbTransportListWatchReleaseSupervisionPolicy",
]
