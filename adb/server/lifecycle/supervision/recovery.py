from __future__ import annotations

from collections.abc import Callable
from random import random
from typing import TypeAlias

from adb._lifecycle import (
    AcquireBlocked,
    AcquireCommitted,
    AcquireExisting,
    AcquireFailed,
    AcquireSuperseded,
)
from adb._recovery import (
    RecoveryAcquired,
    RecoveryAttempt,
    RecoveryAttemptOutcome,
    RecoveryDecisionCore,
    RecoveryExhausted,
    RecoveryFailed,
    RecoveryRetryConfiguration,
)
from adb.server.failure import AdbServerLaunchFailure
from adb.server.generation import AdbServerGeneration
from adb.server.lifecycle.contract import AdbServerAccess, AdbServerAcquireOutcome
from adb.server.lifecycle.supervision.policy import AdbServerRecoveryPolicy


_RandomSource = Callable[[], float]

AdbServerRecoveryFailureCause: TypeAlias = AdbServerLaunchFailure
AdbServerRecoveryResult: TypeAlias = (
    RecoveryAcquired | RecoveryFailed[AdbServerRecoveryFailureCause]
)
AdbServerRecoveryDecision: TypeAlias = RecoveryAttempt | AdbServerRecoveryResult


def _configuration_from_policy(policy: AdbServerRecoveryPolicy) -> RecoveryRetryConfiguration:
    return RecoveryRetryConfiguration(
        retry_initial_seconds=policy.retry_initial_seconds,
        retry_max_seconds=policy.retry_max_seconds,
        retry_multiplier=policy.retry_multiplier,
        retry_jitter_ratio=policy.retry_jitter_ratio,
        deferred_retry_seconds=policy.deferred_retry_seconds,
        max_attempts=policy.max_attempts,
    )


class AdbServerRecovery:
    """Decision engine for one bounded ADB server recovery cycle."""

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
        self._core = RecoveryDecisionCore(
            _configuration_from_policy(policy),
            _random=_random,
            random_source_error="server recovery random source must return a value in [0, 1]",
        )

    @property
    def attempt_number(self) -> int:
        return self._core.attempt_number

    @property
    def failed_attempts(self) -> int:
        return self._core.failed_attempts

    def begin(self) -> RecoveryAttempt:
        """Select the first immediate acquisition attempt for this recovery cycle."""

        if self._core.attempt_number != 0:
            raise RuntimeError("ADB server recovery has already begun")
        return self._core.begin()

    def decide_after(self, result: AdbServerAcquireOutcome) -> AdbServerRecoveryDecision:
        """Apply retry policy after one selected acquisition attempt completes."""

        if self._core.attempt_number == 0:
            raise RuntimeError("ADB server recovery has not begun")
        if not isinstance(
            result,
            (
                AcquireCommitted,
                AcquireExisting,
                AcquireBlocked,
                AcquireFailed,
                AcquireSuperseded,
            ),
        ):
            raise TypeError("result must be AdbServerAcquireOutcome")
        if isinstance(result, (AcquireCommitted, AcquireExisting)) and not isinstance(
            result.access, AdbServerAccess
        ):
            raise TypeError("server acquire access must be AdbServerAccess")
        if isinstance(result, AcquireFailed) and not isinstance(
            result.failure, AdbServerLaunchFailure
        ):
            raise TypeError("server acquire failure must be AdbServerLaunchFailure")
        if isinstance(result, AcquireSuperseded) and not isinstance(
            result.generation, AdbServerGeneration
        ):
            raise TypeError("server superseded generation must be AdbServerGeneration")

        if isinstance(result, (AcquireCommitted, AcquireExisting)):
            outcome = RecoveryAttemptOutcome.ACQUIRED
        elif isinstance(result, (AcquireBlocked, AcquireSuperseded)):
            outcome = RecoveryAttemptOutcome.DEFERRED
        else:
            outcome = RecoveryAttemptOutcome.FAILED

        decision = self._core.decide_after(outcome)
        if isinstance(decision, (RecoveryAcquired, RecoveryAttempt)):
            return decision
        if isinstance(decision, RecoveryExhausted):
            if not isinstance(result, AcquireFailed) or not isinstance(
                result.failure, AdbServerLaunchFailure
            ):
                raise TypeError("unsupported budget-consuming server acquire outcome")
            return RecoveryFailed(
                failed_attempts=decision.failed_attempts,
                cause=result.failure,
            )
        raise TypeError("unsupported shared server recovery decision")


__all__ = [
    "AdbServerRecovery",
    "AdbServerRecoveryDecision",
    "AdbServerRecoveryFailureCause",
    "AdbServerRecoveryResult",
    "RecoveryAcquired",
    "RecoveryAttempt",
    "RecoveryFailed",
]
