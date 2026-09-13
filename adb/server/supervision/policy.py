from __future__ import annotations

import math
from dataclasses import dataclass
from numbers import Real


@dataclass(frozen=True, slots=True)
class AdbServerAcquireSupervisionPolicy:
    """Retry timing for deferred same-generation ADB server acquire results."""

    deferred_retry_seconds: float = 0.1

    def __post_init__(self) -> None:
        value = self.deferred_retry_seconds
        if isinstance(value, bool) or not isinstance(value, Real):
            raise TypeError(
                "ADB server acquire supervision deferred retry must be a real number"
            )
        normalized = float(value)
        if not math.isfinite(normalized) or normalized <= 0.0:
            raise ValueError(
                "ADB server acquire supervision deferred retry must be finite and "
                "greater than zero"
            )
        object.__setattr__(self, "deferred_retry_seconds", normalized)


@dataclass(frozen=True, slots=True)
class AdbServerReleaseSupervisionPolicy:
    """Retry timing for retryable same-generation ADB server release results."""

    retry_seconds: float = 0.1

    def __post_init__(self) -> None:
        value = self.retry_seconds
        if isinstance(value, bool) or not isinstance(value, Real):
            raise TypeError(
                "ADB server release supervision retry must be a real number"
            )
        normalized = float(value)
        if not math.isfinite(normalized) or normalized <= 0.0:
            raise ValueError(
                "ADB server release supervision retry must be finite and "
                "greater than zero"
            )
        object.__setattr__(self, "retry_seconds", normalized)


__all__ = [
    "AdbServerAcquireSupervisionPolicy",
    "AdbServerReleaseSupervisionPolicy",
]
