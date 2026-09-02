from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from math import isfinite
from numbers import Real
from random import random
from typing import TypeAlias

from adb.server.lifecycle.backend import (
    AdbServerBackendAcquireBlocked,
    AdbServerBackendAcquireFailed,
    AdbServerBackendAcquireInProgress,
    AdbServerBackendAcquireResult,
    AdbServerBackendAcquireSatisfied,
    AdbServerBackendAcquireSucceeded,
)
from adb.server.lifecycle.supervision.policy import AdbServerRecoveryPolicy


_RandomSource = Callable[[], float]
_UsableAcquireResult: TypeAlias = (
    AdbServerBackendAcquireSucceeded | AdbServerBackendAcquireSatisfied
)


@dataclass(frozen=True, slots=True)
class AdbServerRecoveryRetry:
    """Retry-policy decision requesting another acquisition after ``delay_seconds``."""

    delay_seconds: float

    def __post_init__(self) -> None:
        if isinstance(self.delay_seconds, bool) or not isinstance(self.delay_seconds, Real):
            raise TypeError("delay_seconds must be a real number")
        delay = float(self.delay_seconds)
        if not isfinite(delay) or delay <= 0.0:
            raise ValueError("delay_seconds must be finite and greater than zero")
        object.__setattr__(self, "delay_seconds", delay)


@dataclass(frozen=True, slots=True)
class AdbServerRecoveryCompleted:
    """Retry-policy decision reporting a usable backend attachment."""

    acquire: _UsableAcquireResult

    def __post_init__(self) -> None:
        if not isinstance(
            self.acquire,
            (AdbServerBackendAcquireSucceeded, AdbServerBackendAcquireSatisfied),
        ):
            raise TypeError("acquire must be a usable ADB server backend acquire result")


@dataclass(frozen=True, slots=True)
class AdbServerRecoveryExhaust:
    """Retry-policy decision reporting that genuine failures exhausted the retry budget."""

    attempts: int
    acquire: AdbServerBackendAcquireFailed

    def __post_init__(self) -> None:
        if isinstance(self.attempts, bool) or not isinstance(self.attempts, int):
            raise TypeError("attempts must be an integer")
        if self.attempts <= 0:
            raise ValueError("attempts must be greater than zero")
        if not isinstance(self.acquire, AdbServerBackendAcquireFailed):
            raise TypeError("acquire must be AdbServerBackendAcquireFailed")


AdbServerRecoveryDecision: TypeAlias = (
    AdbServerRecoveryRetry | AdbServerRecoveryCompleted | AdbServerRecoveryExhaust
)


class AdbServerRecovery:
    """Retry decision engine for repeated ADB server backend acquisition.

    Recovery owns only retry-policy state: genuine backend failure count, retry backoff, jitter,
    and exhaustion. It deliberately has no lifecycle of its own and has no knowledge of
    authoritative runtime server state, activation, reconciliation, scheduling, threads, attempt
    numbering, or the reason recovery was requested. Its supervisor owns the recovery cycle and
    feeds each raw backend acquisition result into :meth:`decide_after`.
    """

    def __init__(
        self,
        policy: AdbServerRecoveryPolicy,
        *,
        _random: _RandomSource = random,
    ) -> None:
        if not isinstance(policy, AdbServerRecoveryPolicy):
            raise TypeError("policy must be AdbServerRecoveryPolicy")
        if not callable(_random):
            raise TypeError("_random must be callable")
        self._policy = policy
        self._random = _random
        self._failed_attempts = 0

    @property
    def failed_attempts(self) -> int:
        return self._failed_attempts

    def decide_after(self, result: AdbServerBackendAcquireResult) -> AdbServerRecoveryDecision:
        """Apply retry policy to one raw backend acquisition result."""

        if not isinstance(
            result,
            (
                AdbServerBackendAcquireSucceeded,
                AdbServerBackendAcquireSatisfied,
                AdbServerBackendAcquireInProgress,
                AdbServerBackendAcquireBlocked,
                AdbServerBackendAcquireFailed,
            ),
        ):
            raise TypeError("result must be AdbServerBackendAcquireResult")

        if isinstance(
            result,
            (AdbServerBackendAcquireSucceeded, AdbServerBackendAcquireSatisfied),
        ):
            return AdbServerRecoveryCompleted(result)

        if isinstance(result, (AdbServerBackendAcquireInProgress, AdbServerBackendAcquireBlocked)):
            return AdbServerRecoveryRetry(self._policy.deferred_retry_seconds)

        self._failed_attempts += 1
        if (
            self._policy.max_attempts is not None
            and self._failed_attempts >= self._policy.max_attempts
        ):
            return AdbServerRecoveryExhaust(self._failed_attempts, result)

        return AdbServerRecoveryRetry(self._retry_delay(self._failed_attempts))

    def _retry_delay(self, failed_attempts: int) -> float:
        base = min(
            self._policy.retry_initial_seconds
            * (self._policy.retry_multiplier ** max(0, failed_attempts - 1)),
            self._policy.retry_max_seconds,
        )
        sample = self._random()
        if not 0.0 <= sample <= 1.0:
            raise ValueError("server recovery random source must return a value in [0, 1]")
        jitter = self._policy.retry_jitter_ratio
        factor = 1.0 + ((sample * 2.0) - 1.0) * jitter
        return max(base * factor, 1e-6)


__all__ = [
    "AdbServerRecovery",
    "AdbServerRecoveryCompleted",
    "AdbServerRecoveryDecision",
    "AdbServerRecoveryExhaust",
    "AdbServerRecoveryRetry",
]
