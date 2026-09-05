from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from math import isfinite
from numbers import Real
from random import random
from typing import TypeAlias

from adb.server.lifecycle.backend import (
    AdbServerBackendAcquired,
    AdbServerBackendAcquireDeferred,
    AdbServerBackendAcquireFailed,
    AdbServerBackendAlreadyAcquired,
    AdbServerBackendAcquireResult,
)
from adb.server.lifecycle.supervision.policy import AdbServerRecoveryPolicy


_RandomSource = Callable[[], float]


@dataclass(frozen=True, slots=True)
class AdbServerRecoveryAttempt:
    """One acquisition attempt selected by the recovery state machine."""

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


@dataclass(frozen=True, slots=True)
class AdbServerRecoveryAcquired:
    """Terminal decision that this recovery attempt newly established an acquisition."""


AdbServerRecoveryFailureCause: TypeAlias = (
    AdbServerBackendAlreadyAcquired | AdbServerBackendAcquireFailed
)


@dataclass(frozen=True, slots=True)
class AdbServerRecoveryFailed:
    """Terminal recovery result after budget-consuming unsuccessful attempts are exhausted."""

    attempts: int
    cause: AdbServerRecoveryFailureCause

    def __post_init__(self) -> None:
        if isinstance(self.attempts, bool) or not isinstance(self.attempts, int):
            raise TypeError("attempts must be an integer")
        if self.attempts <= 0:
            raise ValueError("attempts must be greater than zero")
        if not isinstance(
            self.cause,
            (AdbServerBackendAlreadyAcquired, AdbServerBackendAcquireFailed),
        ):
            raise TypeError("cause must be a budget-consuming backend acquire outcome")


AdbServerRecoveryResult: TypeAlias = AdbServerRecoveryAcquired | AdbServerRecoveryFailed
AdbServerRecoveryDecision: TypeAlias = AdbServerRecoveryAttempt | AdbServerRecoveryResult


class AdbServerRecovery:
    """Decision engine for one bounded ADB server recovery cycle.

    Tracks retry budget, backoff, jitter, and exhaustion. The supervisor executes each
    selected acquisition attempt and feeds its result back into :meth:`decide_after`.
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
        self._attempt_number = 0
        self._failed_attempts = 0

    @property
    def attempt_number(self) -> int:
        return self._attempt_number

    @property
    def failed_attempts(self) -> int:
        return self._failed_attempts

    def begin(self) -> AdbServerRecoveryAttempt:
        """Select the first immediate acquisition attempt for this recovery cycle."""

        if self._attempt_number != 0:
            raise RuntimeError("ADB server recovery has already begun")
        return self._next_attempt(0.0)

    def decide_after(self, result: AdbServerBackendAcquireResult) -> AdbServerRecoveryDecision:
        """Apply retry policy after one selected acquisition attempt completes."""

        if self._attempt_number == 0:
            raise RuntimeError("ADB server recovery has not begun")

        if not isinstance(
            result,
            (
                AdbServerBackendAcquired,
                AdbServerBackendAlreadyAcquired,
                AdbServerBackendAcquireDeferred,
                AdbServerBackendAcquireFailed,
            ),
        ):
            raise TypeError("result must be AdbServerBackendAcquireResult")

        if isinstance(result, AdbServerBackendAcquired):
            return AdbServerRecoveryAcquired()

        if isinstance(result, AdbServerBackendAcquireDeferred):
            return self._next_attempt(self._policy.deferred_retry_seconds)

        if not isinstance(
            result,
            (AdbServerBackendAlreadyAcquired, AdbServerBackendAcquireFailed),
        ):
            raise TypeError("unsupported budget-consuming backend acquire outcome")

        self._failed_attempts += 1
        if (
            self._policy.max_attempts is not None
            and self._failed_attempts >= self._policy.max_attempts
        ):
            return AdbServerRecoveryFailed(self._failed_attempts, result)

        return self._next_attempt(self._retry_delay(self._failed_attempts))

    def _next_attempt(self, delay_seconds: float) -> AdbServerRecoveryAttempt:
        self._attempt_number += 1
        return AdbServerRecoveryAttempt(self._attempt_number, delay_seconds)

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
    "AdbServerRecoveryAcquired",
    "AdbServerRecoveryAttempt",
    "AdbServerRecoveryDecision",
    "AdbServerRecoveryFailed",
    "AdbServerRecoveryFailureCause",
    "AdbServerRecoveryResult",
]
