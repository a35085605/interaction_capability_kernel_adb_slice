from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from math import isfinite
from numbers import Real
from random import random
from typing import TypeAlias

from adb.transport_list.watch.contract import (
    AdbTransportListWatchAcquireBlocked,
    AdbTransportListWatchAcquireCommitted,
    AdbTransportListWatchAcquireExisting,
    AdbTransportListWatchAcquireFailed,
    AdbTransportListWatchAcquireOutcome,
    AdbTransportListWatchAcquireSuperseded,
)
from adb.transport_list.watch.supervision.policy import (
    AdbTransportListWatchRecoveryPolicy,
)


_RandomSource = Callable[[], float]


@dataclass(frozen=True, slots=True)
class AdbTransportListWatchRecoveryAttempt:
    """One acquisition attempt selected by the watch recovery state machine."""

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
            raise ValueError(
                "delay_seconds must be finite and greater than or equal to zero"
            )
        object.__setattr__(self, "delay_seconds", delay)


@dataclass(frozen=True, slots=True)
class AdbTransportListWatchRecoveryAcquired:
    """Terminal decision that a usable watch acquisition exists after this attempt."""


AdbTransportListWatchRecoveryFailureCause: TypeAlias = AdbTransportListWatchAcquireFailed


@dataclass(frozen=True, slots=True)
class AdbTransportListWatchRecoveryFailed:
    """Terminal recovery result after budget-consuming unsuccessful attempts are exhausted."""

    attempts: int
    cause: AdbTransportListWatchRecoveryFailureCause

    def __post_init__(self) -> None:
        if isinstance(self.attempts, bool) or not isinstance(self.attempts, int):
            raise TypeError("attempts must be an integer")
        if self.attempts <= 0:
            raise ValueError("attempts must be greater than zero")
        if not isinstance(self.cause, AdbTransportListWatchAcquireFailed):
            raise TypeError("cause must be AdbTransportListWatchAcquireFailed")


AdbTransportListWatchRecoveryResult: TypeAlias = (
    AdbTransportListWatchRecoveryAcquired | AdbTransportListWatchRecoveryFailed
)
AdbTransportListWatchRecoveryDecision: TypeAlias = (
    AdbTransportListWatchRecoveryAttempt | AdbTransportListWatchRecoveryResult
)


class AdbTransportListWatchRecovery:
    """Decision engine for one bounded transport-list watch recovery cycle.

    Tracks retry budget, backoff, jitter, and exhaustion. The supervisor executes each
    selected acquisition attempt and feeds its result back into :meth:`decide_after`.
    """

    def __init__(
        self,
        policy: AdbTransportListWatchRecoveryPolicy,
        *,
        _random: _RandomSource = random,
    ) -> None:
        if not isinstance(policy, AdbTransportListWatchRecoveryPolicy):
            raise TypeError("policy must be AdbTransportListWatchRecoveryPolicy")
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

    def begin(self) -> AdbTransportListWatchRecoveryAttempt:
        """Select the first immediate acquisition attempt for this recovery cycle."""

        if self._attempt_number != 0:
            raise RuntimeError("ADB transport-list watch recovery has already begun")
        return self._next_attempt(0.0)

    def decide_after(
        self,
        result: AdbTransportListWatchAcquireOutcome,
    ) -> AdbTransportListWatchRecoveryDecision:
        """Apply retry policy after one selected acquisition attempt completes."""

        if self._attempt_number == 0:
            raise RuntimeError("ADB transport-list watch recovery has not begun")

        if not isinstance(
            result,
            (
                AdbTransportListWatchAcquireCommitted,
                AdbTransportListWatchAcquireExisting,
                AdbTransportListWatchAcquireBlocked,
                AdbTransportListWatchAcquireFailed,
                AdbTransportListWatchAcquireSuperseded,
            ),
        ):
            raise TypeError("result must be AdbTransportListWatchAcquireOutcome")

        if isinstance(
            result,
            (AdbTransportListWatchAcquireCommitted, AdbTransportListWatchAcquireExisting),
        ):
            return AdbTransportListWatchRecoveryAcquired()

        if isinstance(
            result,
            (AdbTransportListWatchAcquireBlocked, AdbTransportListWatchAcquireSuperseded),
        ):
            return self._next_attempt(self._policy.deferred_retry_seconds)

        if not isinstance(result, AdbTransportListWatchAcquireFailed):
            raise TypeError("unsupported budget-consuming transport-list watch acquire outcome")

        self._failed_attempts += 1
        if (
            self._policy.max_attempts is not None
            and self._failed_attempts >= self._policy.max_attempts
        ):
            return AdbTransportListWatchRecoveryFailed(self._failed_attempts, result)

        return self._next_attempt(self._retry_delay(self._failed_attempts))

    def _next_attempt(self, delay_seconds: float) -> AdbTransportListWatchRecoveryAttempt:
        self._attempt_number += 1
        return AdbTransportListWatchRecoveryAttempt(self._attempt_number, delay_seconds)

    def _retry_delay(self, failed_attempts: int) -> float:
        base = min(
            self._policy.retry_initial_seconds
            * (self._policy.retry_multiplier ** max(0, failed_attempts - 1)),
            self._policy.retry_max_seconds,
        )
        sample = self._random()
        if not 0.0 <= sample <= 1.0:
            raise ValueError(
                "transport-list watch recovery random source must return a value in [0, 1]"
            )
        jitter = self._policy.retry_jitter_ratio
        factor = 1.0 + ((sample * 2.0) - 1.0) * jitter
        return max(base * factor, 1e-6)


__all__ = [
    "AdbTransportListWatchRecovery",
    "AdbTransportListWatchRecoveryAcquired",
    "AdbTransportListWatchRecoveryAttempt",
    "AdbTransportListWatchRecoveryDecision",
    "AdbTransportListWatchRecoveryFailed",
    "AdbTransportListWatchRecoveryFailureCause",
    "AdbTransportListWatchRecoveryResult",
]
