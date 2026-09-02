from __future__ import annotations

from dataclasses import dataclass
from typing import TypeAlias

from adb.server.failure import AdbServerLaunchFailure
from adb.server.identity import AdbServerIdentity
from adb.server.lifecycle.control.backend import (
    AdbServerBackendAcquireBlocked,
    AdbServerBackendAcquireFailed,
    AdbServerBackendAcquireInProgress,
)
from adb.server.lifecycle.control.result import (
    AdbServerProvisionDeferred,
    AdbServerProvisionFailed,
)
from adb.server.lifecycle.supervision.intent import (
    AdbServerEnsureIntent,
    AdbServerEnsureIntentResult,
    AdbServerEnsureSatisfied,
    AdbServerLifecycleIntent,
    AdbServerLifecycleIntentResult,
    AdbServerReconcileIntent,
)
from adb.server.lifecycle.transaction import (
    AdbServerProvisionAcquireStopped,
    AdbServerProvisionActivationAttempted,
    AdbServerProvisionStateConflict,
    AdbServerProvisionTransactionResult,
)
from adb.server.signal import AdbServerReconciliationRequested
from adb.server.state import (
    AdbServerActivated,
    AdbServerActivationRejected,
    AdbServerDeactivated,
    AdbServerState,
)


@dataclass(frozen=True, slots=True)
class AdbServerProvisionAction:
    """Semantic request to run one authoritative runtime provisioning transaction."""


@dataclass(frozen=True, slots=True)
class AdbServerRetireAction:
    """Retire the authoritative lifetime matching an optional identity fence."""

    expected_server: AdbServerIdentity | None = None

    def __post_init__(self) -> None:
        if self.expected_server is not None and not isinstance(
            self.expected_server, AdbServerIdentity
        ):
            raise TypeError("expected_server must be AdbServerIdentity or None")


AdbServerLifecycleAction: TypeAlias = AdbServerProvisionAction | AdbServerRetireAction
AdbServerLifecycleIntentTransition: TypeAlias = (
    AdbServerLifecycleAction | AdbServerEnsureSatisfied
)
AdbServerLifecycleActionResult: TypeAlias = (
    AdbServerProvisionTransactionResult | AdbServerDeactivated | None
)


def transition_lifecycle_intent(
    intent: AdbServerLifecycleIntent,
    state: AdbServerState,
) -> AdbServerLifecycleIntentTransition:
    """Purely interpret one lifecycle intent against immutable authoritative state evidence."""

    if not isinstance(state, AdbServerState):
        raise TypeError("state must be AdbServerState")

    if isinstance(intent, AdbServerEnsureIntent):
        if state.active:
            return AdbServerEnsureSatisfied()
        return AdbServerProvisionAction()

    if isinstance(intent, AdbServerReconcileIntent):
        cause = intent.cause
        expected_server = (
            cause.server if isinstance(cause, AdbServerReconciliationRequested) else None
        )
        return AdbServerRetireAction(expected_server)

    raise TypeError("unsupported ADB server lifecycle intent")


def _backend_busy_diagnostic(
    result: AdbServerBackendAcquireInProgress | AdbServerBackendAcquireBlocked,
) -> str:
    if isinstance(result, AdbServerBackendAcquireInProgress):
        return result.diagnostic or "ADB server backend acquire is already in progress"
    return result.diagnostic


def transition_provision_result(
    result: AdbServerProvisionTransactionResult,
) -> AdbServerEnsureIntentResult:
    """Interpret raw runtime provisioning evidence into supervision-level ensure semantics."""

    if isinstance(result, AdbServerProvisionStateConflict):
        if result.state.active:
            return AdbServerEnsureSatisfied()
        return AdbServerProvisionDeferred(
            "authoritative ADB server state prevented provisioning"
        )

    if isinstance(result, AdbServerProvisionAcquireStopped):
        acquire = result.acquire
        if isinstance(
            acquire,
            (AdbServerBackendAcquireInProgress, AdbServerBackendAcquireBlocked),
        ):
            return AdbServerProvisionDeferred(_backend_busy_diagnostic(acquire))
        if isinstance(acquire, AdbServerBackendAcquireFailed):
            return AdbServerProvisionFailed(acquire.diagnostic)
        raise TypeError("provision acquire-stopped result contains unsupported acquire evidence")

    if isinstance(result, AdbServerProvisionActivationAttempted):
        activation = result.activation
        if isinstance(activation, AdbServerActivated):
            return AdbServerEnsureSatisfied(activation)
        if isinstance(activation, AdbServerActivationRejected):
            if activation.state.active:
                return AdbServerEnsureSatisfied()
            return AdbServerProvisionDeferred(
                "ADB runtime server state changed before acquired endpoint could commit"
            )
        raise TypeError(
            "provision activation-attempted result contains unsupported activation evidence"
        )

    raise TypeError("result must be an ADB server provision transaction result")


def transition_lifecycle_result(
    action: AdbServerLifecycleAction,
    result: AdbServerLifecycleActionResult,
) -> AdbServerLifecycleIntentResult:
    """Purely interpret one runtime transaction result into its supervision-level result."""

    if isinstance(action, AdbServerProvisionAction):
        if not isinstance(
            result,
            (
                AdbServerProvisionStateConflict,
                AdbServerProvisionAcquireStopped,
                AdbServerProvisionActivationAttempted,
            ),
        ):
            raise TypeError("provision action requires an ADB server provision transaction result")
        return transition_provision_result(result)

    if isinstance(action, AdbServerRetireAction):
        if result is None or isinstance(result, AdbServerDeactivated):
            return result
        raise TypeError("retire action requires AdbServerDeactivated or None")

    raise TypeError("action must be an ADB server lifecycle action")


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
    """The ensure intent is satisfied, optionally by a newly committed activation."""

    activation: AdbServerActivated | None = None

    def __post_init__(self) -> None:
        if self.activation is not None and not isinstance(
            self.activation, AdbServerActivated
        ):
            raise TypeError("activation must be AdbServerActivated or None")


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
    result: AdbServerEnsureIntentResult,
    *,
    max_attempts: int | None,
) -> AdbServerRecoveryTransition:
    """Pure recovery transition from immutable attempt state and one ensure-intent result."""

    if not isinstance(attempt, AdbServerRecoveryAttempt):
        raise TypeError("attempt must be AdbServerRecoveryAttempt")
    if max_attempts is not None:
        if isinstance(max_attempts, bool) or not isinstance(max_attempts, int):
            raise TypeError("max_attempts must be an integer or None")
        if max_attempts <= 0:
            raise ValueError("max_attempts must be greater than zero")

    if isinstance(result, AdbServerEnsureSatisfied):
        return AdbServerRecoverySucceeded(result.activation)

    if isinstance(result, AdbServerProvisionDeferred):
        return AdbServerRecoveryDefer(attempt.after_deferral())

    if isinstance(result, AdbServerProvisionFailed):
        failure = AdbServerLaunchFailure(result.diagnostic)
        next_attempt = attempt.after_launch_failure()
        launch_attempts = next_attempt.launch_attempts
        if max_attempts is not None and launch_attempts >= max_attempts:
            return AdbServerRecoveryExhaust(launch_attempts, failure)
        return AdbServerRecoveryRetry(next_attempt, failure)

    raise TypeError("result must be AdbServerEnsureIntentResult")


__all__ = [
    "AdbServerLifecycleAction",
    "AdbServerLifecycleActionResult",
    "AdbServerLifecycleIntentTransition",
    "AdbServerProvisionAction",
    "AdbServerRetireAction",
    "AdbServerRecoverySucceeded",
    "AdbServerRecoveryAttempt",
    "AdbServerRecoveryDefer",
    "AdbServerRecoveryExhaust",
    "AdbServerRecoveryRetry",
    "AdbServerRecoveryTransition",
    "transition_lifecycle_intent",
    "transition_lifecycle_result",
    "transition_provision_result",
    "transition_recovery",
]
