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
from adb.server.signal import (
    AdbServerLost,
    AdbServerReconciliationRequested,
    AdbServerRecovered,
    AdbServerRetired,
)


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
    """Ensure intent is satisfied; ``recovered`` exists only for a newly committed lifetime."""

    recovered: AdbServerRecovered | None = None

    def __post_init__(self) -> None:
        if self.recovered is not None and not isinstance(self.recovered, AdbServerRecovered):
            raise TypeError("recovered must be AdbServerRecovered or None")


@dataclass(frozen=True, slots=True)
class AdbServerReconcileCompleted:
    """Lifecycle signals produced by one authoritative retirement transaction."""

    retired: AdbServerRetired
    lost: AdbServerLost

    def __post_init__(self) -> None:
        if not isinstance(self.retired, AdbServerRetired):
            raise TypeError("retired must be AdbServerRetired")
        if not isinstance(self.lost, AdbServerLost):
            raise TypeError("lost must be AdbServerLost")
        if self.retired.server != self.lost.server:
            raise ValueError("retired and lost signals must describe the same server lifetime")


AdbServerEnsureIntentResult: TypeAlias = (
    AdbServerEnsureSatisfied | AdbServerProvisionDeferred | AdbServerProvisionFailed
)
AdbServerReconcileIntentResult: TypeAlias = AdbServerReconcileCompleted | None
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
    "AdbServerReconcileCompleted",
    "AdbServerReconcileIntent",
    "AdbServerReconcileIntentResult",
]
