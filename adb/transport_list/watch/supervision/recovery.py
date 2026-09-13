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
from adb.transport_list.watch.generation import AdbTransportListWatchGeneration
from adb.transport_list.watch.lifecycle import (
    AdbTransportListWatchAcquireAlreadyActive,
    AdbTransportListWatchAcquireFailed,
    AdbTransportListWatchAcquireReleaseRequired,
    AdbTransportListWatchAcquireRequestMismatch,
    AdbTransportListWatchAcquireResult,
    AdbTransportListWatchAcquireSucceeded,
    AdbTransportListWatchGenerationMismatch,
    AdbTransportListWatchLifecycleBusy,
)
from adb.transport_list.watch.request import AdbTransportListWatchRequest
from adb.transport_list.watch.stream import AdbTransportListWatchStream
from adb.transport_list.watch.supervision.policy import AdbTransportListWatchRecoveryPolicy
from adb.transport_list.watch.template import AdbTransportListWatchAcquireError


_RandomSource = Callable[[], float]

AdbTransportListWatchRecoveryFailureCause: TypeAlias = AdbTransportListWatchAcquireError
AdbTransportListWatchRecoveryResult: TypeAlias = (
    RecoveryAcquired | RecoveryFailed[AdbTransportListWatchRecoveryFailureCause]
)
AdbTransportListWatchRecoveryDecision: TypeAlias = (
    RecoveryAttempt | AdbTransportListWatchRecoveryResult
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


def _validate_active_result(
    result: AdbTransportListWatchAcquireSucceeded | AdbTransportListWatchAcquireAlreadyActive,
) -> None:
    snapshot = result.snapshot
    if not isinstance(snapshot.generation, AdbTransportListWatchGeneration):
        raise TypeError("watch acquire generation must be AdbTransportListWatchGeneration")
    if not isinstance(snapshot.request, AdbTransportListWatchRequest):
        raise TypeError("watch acquire request must be AdbTransportListWatchRequest")
    if not isinstance(snapshot.capability, AdbTransportListWatchStream):
        raise TypeError("watch acquire capability must satisfy AdbTransportListWatchStream")


def _failure_cause(
    result: AdbTransportListWatchAcquireFailed | AdbTransportListWatchAcquireReleaseRequired,
) -> AdbTransportListWatchAcquireError:
    snapshot = result.snapshot
    if not isinstance(snapshot.generation, AdbTransportListWatchGeneration):
        raise TypeError("watch acquire generation must be AdbTransportListWatchGeneration")
    if not isinstance(snapshot.request, AdbTransportListWatchRequest):
        raise TypeError("watch acquire request must be AdbTransportListWatchRequest")
    if not isinstance(snapshot.last_error, AdbTransportListWatchAcquireError):
        raise TypeError("watch acquire failure must be AdbTransportListWatchAcquireError")
    return snapshot.last_error


class AdbTransportListWatchRecovery:
    """Decision engine for one bounded transport-list watch recovery cycle."""

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

    def begin(self) -> RecoveryAttempt:
        if self._core.attempt_number != 0:
            raise RuntimeError("ADB transport-list watch recovery has already begun")
        return self._core.begin()

    def decide_after(
        self,
        result: AdbTransportListWatchAcquireResult,
    ) -> AdbTransportListWatchRecoveryDecision:
        if self._core.attempt_number == 0:
            raise RuntimeError("ADB transport-list watch recovery has not begun")
        if not isinstance(
            result,
            (
                AdbTransportListWatchAcquireSucceeded,
                AdbTransportListWatchAcquireAlreadyActive,
                AdbTransportListWatchAcquireFailed,
                AdbTransportListWatchAcquireReleaseRequired,
                AdbTransportListWatchAcquireRequestMismatch,
                AdbTransportListWatchGenerationMismatch,
                AdbTransportListWatchLifecycleBusy,
            ),
        ):
            raise TypeError("result must be AdbTransportListWatchAcquireResult")

        cause: AdbTransportListWatchAcquireError | None = None
        if isinstance(
            result,
            (AdbTransportListWatchAcquireSucceeded, AdbTransportListWatchAcquireAlreadyActive),
        ):
            _validate_active_result(result)
            outcome = RecoveryAttemptOutcome.ACQUIRED
        elif isinstance(result, AdbTransportListWatchGenerationMismatch):
            if not isinstance(result.current_generation, AdbTransportListWatchGeneration):
                raise TypeError("watch current generation must be AdbTransportListWatchGeneration")
            outcome = RecoveryAttemptOutcome.DEFERRED
        elif isinstance(result, AdbTransportListWatchAcquireRequestMismatch):
            if not isinstance(result.current_request, AdbTransportListWatchRequest):
                raise TypeError("watch current request must be AdbTransportListWatchRequest")
            outcome = RecoveryAttemptOutcome.DEFERRED
        elif isinstance(result, AdbTransportListWatchLifecycleBusy):
            outcome = RecoveryAttemptOutcome.DEFERRED
        else:
            cause = _failure_cause(result)
            outcome = RecoveryAttemptOutcome.FAILED

        decision = self._core.decide_after(outcome)
        if isinstance(decision, (RecoveryAcquired, RecoveryAttempt)):
            return decision
        if isinstance(decision, RecoveryExhausted):
            if cause is None:
                raise TypeError("unsupported budget-consuming watch acquire outcome")
            return RecoveryFailed(
                failed_attempts=decision.failed_attempts,
                cause=cause,
            )
        raise TypeError("unsupported shared transport-list watch recovery decision")


__all__ = [
    "AdbTransportListWatchRecovery",
    "AdbTransportListWatchRecoveryDecision",
    "AdbTransportListWatchRecoveryFailureCause",
    "AdbTransportListWatchRecoveryResult",
    "RecoveryAcquired",
    "RecoveryAttempt",
    "RecoveryFailed",
]
