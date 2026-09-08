from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from math import isfinite
from numbers import Real
from random import random
from typing import TypeAlias

from adb._recovery import (
    RecoveryAcquired,
    RecoveryAttempt,
    RecoveryAttemptOutcome,
    RecoveryDecisionCore,
    RecoveryExhausted,
    RecoveryRetryConfiguration,
)
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


def _configuration_from_policy(
    policy: AdbTransportListWatchRecoveryPolicy,
) -> RecoveryRetryConfiguration:
    return RecoveryRetryConfiguration(
        retry_initial_seconds=policy.retry_initial_seconds,
        retry_max_seconds=policy.retry_max_seconds,
        retry_multiplier=policy.retry_multiplier,
        retry_jitter_ratio=policy.retry_jitter_ratio,
        deferred_retry_seconds=policy.deferred_retry_seconds,
        max_attempts=policy.max_attempts,
    )


def _domain_attempt(attempt: RecoveryAttempt) -> AdbTransportListWatchRecoveryAttempt:
    return AdbTransportListWatchRecoveryAttempt(
        attempt.attempt_number,
        attempt.delay_seconds,
    )


class AdbTransportListWatchRecovery:
    """Decision engine for one bounded transport-list watch recovery cycle.

    Domain acquire outcomes are mapped onto a shared retry decision core. The watch-facing
    recovery types remain responsible for preserving watch failure causes and public contracts.
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
        self._core = RecoveryDecisionCore(
            _configuration_from_policy(policy),
            _random=_random,
            random_source_error=(
                "transport-list watch recovery random source must return a value in [0, 1]"
            ),
        )

    @property
    def attempt_number(self) -> int:
        return self._core.attempt_number

    @property
    def failed_attempts(self) -> int:
        return self._core.failed_attempts

    def begin(self) -> AdbTransportListWatchRecoveryAttempt:
        """Select the first immediate acquisition attempt for this recovery cycle."""

        if self._core.attempt_number != 0:
            raise RuntimeError("ADB transport-list watch recovery has already begun")
        return _domain_attempt(self._core.begin())

    def decide_after(
        self,
        result: AdbTransportListWatchAcquireOutcome,
    ) -> AdbTransportListWatchRecoveryDecision:
        """Apply retry policy after one selected acquisition attempt completes."""

        if self._core.attempt_number == 0:
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
            outcome = RecoveryAttemptOutcome.ACQUIRED
        elif isinstance(
            result,
            (AdbTransportListWatchAcquireBlocked, AdbTransportListWatchAcquireSuperseded),
        ):
            outcome = RecoveryAttemptOutcome.DEFERRED
        else:
            outcome = RecoveryAttemptOutcome.FAILED

        decision = self._core.decide_after(outcome)
        if isinstance(decision, RecoveryAcquired):
            return AdbTransportListWatchRecoveryAcquired()
        if isinstance(decision, RecoveryAttempt):
            return _domain_attempt(decision)
        if isinstance(decision, RecoveryExhausted):
            if not isinstance(result, AdbTransportListWatchAcquireFailed):
                raise TypeError(
                    "unsupported budget-consuming transport-list watch acquire outcome"
                )
            return AdbTransportListWatchRecoveryFailed(decision.failed_attempts, result)
        raise TypeError("unsupported shared transport-list watch recovery decision")


__all__ = [
    "AdbTransportListWatchRecovery",
    "AdbTransportListWatchRecoveryAcquired",
    "AdbTransportListWatchRecoveryAttempt",
    "AdbTransportListWatchRecoveryDecision",
    "AdbTransportListWatchRecoveryFailed",
    "AdbTransportListWatchRecoveryFailureCause",
    "AdbTransportListWatchRecoveryResult",
]
