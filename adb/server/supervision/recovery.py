from __future__ import annotations

from collections.abc import Callable
from random import random
from typing import TypeAlias

from adb._recovery import (
    RecoveryAcquired,
    RecoveryAttempt,
    RecoveryAttemptOutcome,
    RecoveryDecisionCore,
    RecoveryExhausted,
    RecoveryFailed,
    RecoveryRetryConfiguration,
)
from adb.server.capability import AdbServerCapability
from adb.server.generation import AdbServerGeneration
from adb.server.lifecycle import (
    AdbServerAcquireAlreadyActive,
    AdbServerAcquireFailed,
    AdbServerAcquireReleaseRequired,
    AdbServerAcquireRequestMismatch,
    AdbServerAcquireResult,
    AdbServerAcquireSucceeded,
    AdbServerGenerationMismatch,
    AdbServerLifecycleBusy,
)
from adb.server.request import AdbServerRequest
from adb.server.supervision.policy import AdbServerRecoveryPolicy
from adb.server.template import AdbServerAcquireError


_RandomSource = Callable[[], float]

AdbServerRecoveryFailureCause: TypeAlias = AdbServerAcquireError
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


def _validate_active_result(
    result: AdbServerAcquireSucceeded | AdbServerAcquireAlreadyActive,
) -> None:
    snapshot = result.snapshot
    if not isinstance(snapshot.generation, AdbServerGeneration):
        raise TypeError("server acquire generation must be AdbServerGeneration")
    if not isinstance(snapshot.request, AdbServerRequest):
        raise TypeError("server acquire request must be AdbServerRequest")
    if not isinstance(snapshot.capability, AdbServerCapability):
        raise TypeError("server acquire capability must be AdbServerCapability")


def _failure_cause(
    result: AdbServerAcquireFailed | AdbServerAcquireReleaseRequired,
) -> AdbServerAcquireError:
    snapshot = result.snapshot
    if not isinstance(snapshot.generation, AdbServerGeneration):
        raise TypeError("server acquire generation must be AdbServerGeneration")
    if not isinstance(snapshot.request, AdbServerRequest):
        raise TypeError("server acquire request must be AdbServerRequest")
    if not isinstance(snapshot.last_error, AdbServerAcquireError):
        raise TypeError("server acquire failure must be AdbServerAcquireError")
    return snapshot.last_error


class AdbServerRecovery:
    """Decision engine for one bounded ADB server recovery cycle.

    The recovery policy consumes the simplified synchronous lifecycle outcomes directly.
    A failed acquisition (including a pre-existing ``RELEASE_REQUIRED`` state) consumes
    failure budget. The orchestration layer is responsible for completing the matching
    release before the next selected acquisition attempt is executed.
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

    def decide_after(self, result: AdbServerAcquireResult) -> AdbServerRecoveryDecision:
        """Apply retry policy after one simplified lifecycle acquisition result."""

        if self._core.attempt_number == 0:
            raise RuntimeError("ADB server recovery has not begun")
        if not isinstance(
            result,
            (
                AdbServerAcquireSucceeded,
                AdbServerAcquireAlreadyActive,
                AdbServerAcquireFailed,
                AdbServerAcquireReleaseRequired,
                AdbServerAcquireRequestMismatch,
                AdbServerGenerationMismatch,
                AdbServerLifecycleBusy,
            ),
        ):
            raise TypeError("result must be AdbServerAcquireResult")

        cause: AdbServerAcquireError | None = None
        if isinstance(result, (AdbServerAcquireSucceeded, AdbServerAcquireAlreadyActive)):
            _validate_active_result(result)
            outcome = RecoveryAttemptOutcome.ACQUIRED
        elif isinstance(result, AdbServerGenerationMismatch):
            if not isinstance(result.current_generation, AdbServerGeneration):
                raise TypeError("server current generation must be AdbServerGeneration")
            outcome = RecoveryAttemptOutcome.DEFERRED
        elif isinstance(result, AdbServerAcquireRequestMismatch):
            if not isinstance(result.current_request, AdbServerRequest):
                raise TypeError("server current request must be AdbServerRequest")
            outcome = RecoveryAttemptOutcome.DEFERRED
        elif isinstance(result, AdbServerLifecycleBusy):
            outcome = RecoveryAttemptOutcome.DEFERRED
        else:
            cause = _failure_cause(result)
            outcome = RecoveryAttemptOutcome.FAILED

        decision = self._core.decide_after(outcome)
        if isinstance(decision, (RecoveryAcquired, RecoveryAttempt)):
            return decision
        if isinstance(decision, RecoveryExhausted):
            if cause is None:
                raise TypeError("unsupported budget-consuming server acquire outcome")
            return RecoveryFailed(
                failed_attempts=decision.failed_attempts,
                cause=cause,
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
