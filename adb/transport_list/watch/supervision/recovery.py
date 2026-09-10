from __future__ import annotations

from collections.abc import Callable
from random import random
from typing import TypeAlias

from adb._lifecycle import (
    AcquireAccessMismatch,
    AcquireBlocked,
    AcquireCommitted,
    AcquireExisting,
    AcquireFailed,
    GenerationMismatch,
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
from adb.transport_list.watch.lifecycle import (
    AdbTransportListWatchAccess,
    AdbTransportListWatchAcquireOutcome,
)
from adb.transport_list.watch.generation import AdbTransportListWatchGeneration
from adb.transport_list.watch.failure import AdbTransportListWatchFailure
from adb.transport_list.watch.stream import AdbTransportListWatchStream
from adb.transport_list.watch.supervision.policy import AdbTransportListWatchRecoveryPolicy


_RandomSource = Callable[[], float]

AdbTransportListWatchRecoveryFailureCause: TypeAlias = AdbTransportListWatchFailure
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
        """Select the first immediate acquisition attempt for this recovery cycle."""

        if self._core.attempt_number != 0:
            raise RuntimeError("ADB transport-list watch recovery has already begun")
        return self._core.begin()

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
                AcquireCommitted,
                AcquireExisting,
                AcquireAccessMismatch,
                GenerationMismatch,
                AcquireBlocked,
                AcquireFailed,
                AcquireSuperseded,
            ),
        ):
            raise TypeError("result must be AdbTransportListWatchAcquireOutcome")

        if isinstance(result, (AcquireCommitted, AcquireExisting)):
            snapshot = result.snapshot
            if not isinstance(snapshot.generation, AdbTransportListWatchGeneration):
                raise TypeError(
                    "watch acquire generation must be AdbTransportListWatchGeneration"
                )
            if snapshot.access is not None and not isinstance(
                snapshot.access, AdbTransportListWatchAccess
            ):
                raise TypeError("watch acquire access must be AdbTransportListWatchAccess or None")
            if snapshot.capability is not None and not isinstance(
                snapshot.capability, AdbTransportListWatchStream
            ):
                raise TypeError(
                    "watch acquire capability must satisfy AdbTransportListWatchStream or be None"
                )

        if isinstance(result, AcquireAccessMismatch) and not isinstance(
            result.current_access, AdbTransportListWatchAccess
        ):
            raise TypeError("watch current access must be AdbTransportListWatchAccess")
        if isinstance(result, GenerationMismatch) and not isinstance(
            result.current_generation, AdbTransportListWatchGeneration
        ):
            raise TypeError(
                "watch current generation must be AdbTransportListWatchGeneration"
            )
        if isinstance(result, AcquireFailed) and not isinstance(
            result.failure, AdbTransportListWatchFailure
        ):
            raise TypeError("watch acquire failure must be AdbTransportListWatchFailure")
        if isinstance(result, AcquireSuperseded) and not isinstance(
            result.current_generation, AdbTransportListWatchGeneration
        ):
            raise TypeError(
                "watch superseded current generation must be AdbTransportListWatchGeneration"
            )

        if isinstance(result, (AcquireCommitted, AcquireExisting)):
            outcome = RecoveryAttemptOutcome.ACQUIRED
        elif isinstance(
            result,
            (AcquireAccessMismatch, GenerationMismatch, AcquireBlocked, AcquireSuperseded),
        ):
            outcome = RecoveryAttemptOutcome.DEFERRED
        else:
            outcome = RecoveryAttemptOutcome.FAILED

        decision = self._core.decide_after(outcome)
        if isinstance(decision, (RecoveryAcquired, RecoveryAttempt)):
            return decision
        if isinstance(decision, RecoveryExhausted):
            if not isinstance(result, AcquireFailed) or not isinstance(
                result.failure, AdbTransportListWatchFailure
            ):
                raise TypeError(
                    "unsupported budget-consuming transport-list watch acquire outcome"
                )
            return RecoveryFailed(
                failed_attempts=decision.failed_attempts,
                cause=result.failure,
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
