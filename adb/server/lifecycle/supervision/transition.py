from __future__ import annotations

from dataclasses import dataclass
from typing import TypeAlias

from adb.server.failure import AdbServerLaunchFailure
from adb.server.lifetime import AdbServerLifetime
from adb.server.lifecycle.control.result import (
    AdbServerProvisionDeferred,
    AdbServerProvisionFailed,
)
from adb.server.lifecycle.transaction import (
    AdbServerProvisionCommitted,
    AdbServerProvisionTransactionResult,
)


@dataclass(frozen=True, slots=True)
class AdbServerRecoveryAttempt:
    """Immutable recovery progress carried from one provisioning attempt to the next."""

    attempt_number: int
    launch_attempts: int = 0

    def __post_init__(self) -> None:
        if isinstance(self.attempt_number, bool) or not isinstance(self.attempt_number, int):
            raise TypeError("attempt_number must be an integer")
        if self.attempt_number <= 0:
            raise ValueError("attempt_number must be greater than zero")
        if isinstance(self.launch_attempts, bool) or not isinstance(self.launch_attempts, int):
            raise TypeError("launch_attempts must be an integer")
        if self.launch_attempts < 0:
            raise ValueError("launch_attempts must be greater than or equal to zero")

    def after_deferral(self) -> "AdbServerRecoveryAttempt":
        return AdbServerRecoveryAttempt(
            attempt_number=self.attempt_number + 1,
            launch_attempts=self.launch_attempts,
        )

    def after_launch_failure(self) -> "AdbServerRecoveryAttempt":
        return AdbServerRecoveryAttempt(
            attempt_number=self.attempt_number + 1,
            launch_attempts=self.launch_attempts + 1,
        )


@dataclass(frozen=True, slots=True)
class AdbServerRecoverySucceeded:
    """A provisioned server has already committed as the authoritative runtime lifetime."""

    server: AdbServerLifetime


@dataclass(frozen=True, slots=True)
class AdbServerRecoveryDefer:
    """Provisioning deferral that preserves launch-attempt budget."""

    next_attempt: AdbServerRecoveryAttempt


@dataclass(frozen=True, slots=True)
class AdbServerRecoveryRetry:
    """A launch failure consumed budget and another attempt remains."""

    next_attempt: AdbServerRecoveryAttempt
    failure: AdbServerLaunchFailure


@dataclass(frozen=True, slots=True)
class AdbServerRecoveryExhaust:
    """A launch failure consumed the final allowed launch attempt."""

    attempts: int
    failure: AdbServerLaunchFailure


AdbServerRecoveryTransition: TypeAlias = (
    AdbServerRecoverySucceeded
    | AdbServerRecoveryDefer
    | AdbServerRecoveryRetry
    | AdbServerRecoveryExhaust
)


def transition_recovery(
    attempt: AdbServerRecoveryAttempt,
    result: AdbServerProvisionTransactionResult,
    *,
    max_attempts: int | None,
) -> AdbServerRecoveryTransition:
    """Pure recovery transition from immutable attempt state and one provision result."""

    if not isinstance(attempt, AdbServerRecoveryAttempt):
        raise TypeError("attempt must be AdbServerRecoveryAttempt")
    if max_attempts is not None:
        if isinstance(max_attempts, bool) or not isinstance(max_attempts, int):
            raise TypeError("max_attempts must be an integer or None")
        if max_attempts <= 0:
            raise ValueError("max_attempts must be greater than zero")

    if isinstance(result, AdbServerProvisionCommitted):
        return AdbServerRecoverySucceeded(result.server)

    if isinstance(result, AdbServerProvisionDeferred):
        return AdbServerRecoveryDefer(attempt.after_deferral())

    if isinstance(result, AdbServerProvisionFailed):
        failure = AdbServerLaunchFailure(result.diagnostic)
        next_attempt = attempt.after_launch_failure()
        launch_attempts = next_attempt.launch_attempts
        if max_attempts is not None and launch_attempts >= max_attempts:
            return AdbServerRecoveryExhaust(launch_attempts, failure)
        return AdbServerRecoveryRetry(next_attempt, failure)

    raise TypeError("result must be AdbServerProvisionTransactionResult")


__all__ = [
    "AdbServerRecoverySucceeded",
    "AdbServerRecoveryAttempt",
    "AdbServerRecoveryDefer",
    "AdbServerRecoveryExhaust",
    "AdbServerRecoveryRetry",
    "AdbServerRecoveryTransition",
    "transition_recovery",
]
