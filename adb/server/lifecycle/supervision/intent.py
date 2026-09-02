from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, TypeAlias, overload, runtime_checkable

from adb.server.failure import (
    AdbServerConnectionFailure,
    AdbServerLivenessFailure,
    AdbServerProcessExitedFailure,
)
from adb.server.lifecycle.control.result import (
    AdbServerProvisionDeferred,
    AdbServerProvisionFailed,
)
from adb.server.signal import AdbServerReconciliationRequested
from adb.server.state import AdbServerActivated, AdbServerDeactivated


@dataclass(frozen=True, slots=True)
class AdbServerEnsureIntent:
    """Request reconciliation to an authoritative active ADB server lifetime."""


@dataclass(frozen=True, slots=True)
class AdbServerReconcileIntent:
    """Request retirement after terminal liveness failure without interpreting server identity."""

    cause: AdbServerReconciliationRequested | AdbServerLivenessFailure

    def __post_init__(self) -> None:
        if not isinstance(
            self.cause,
            (
                AdbServerReconciliationRequested,
                AdbServerConnectionFailure,
                AdbServerProcessExitedFailure,
            ),
        ):
            raise TypeError(
                "cause must be AdbServerReconciliationRequested or AdbServerLivenessFailure"
            )


@dataclass(frozen=True, slots=True)
class AdbServerEnsureSatisfied:
    """Ensure is satisfied; ``activation`` exists only when this ensure committed a new lifetime."""

    activation: AdbServerActivated | None = None

    def __post_init__(self) -> None:
        if self.activation is not None and not isinstance(
            self.activation, AdbServerActivated
        ):
            raise TypeError("activation must be AdbServerActivated or None")


AdbServerEnsureIntentResult: TypeAlias = (
    AdbServerEnsureSatisfied | AdbServerProvisionDeferred | AdbServerProvisionFailed
)
AdbServerReconcileIntentResult: TypeAlias = AdbServerDeactivated | None
AdbServerLifecycleIntent: TypeAlias = AdbServerEnsureIntent | AdbServerReconcileIntent
AdbServerLifecycleIntentResult: TypeAlias = (
    AdbServerEnsureIntentResult | AdbServerReconcileIntentResult
)


@runtime_checkable
class AdbServerLifecycleIntentDispatcher(Protocol):
    """Lifecycle command port used by supervision without exposing runtime state or identity."""

    @overload
    def dispatch(
        self,
        intent: AdbServerEnsureIntent,
    ) -> AdbServerEnsureIntentResult: ...

    @overload
    def dispatch(
        self,
        intent: AdbServerReconcileIntent,
    ) -> AdbServerReconcileIntentResult: ...

    def dispatch(
        self,
        intent: AdbServerLifecycleIntent,
    ) -> AdbServerLifecycleIntentResult: ...


__all__ = [
    "AdbServerEnsureIntent",
    "AdbServerEnsureIntentResult",
    "AdbServerEnsureSatisfied",
    "AdbServerLifecycleIntent",
    "AdbServerLifecycleIntentDispatcher",
    "AdbServerLifecycleIntentResult",
    "AdbServerReconcileIntent",
    "AdbServerReconcileIntentResult",
]
