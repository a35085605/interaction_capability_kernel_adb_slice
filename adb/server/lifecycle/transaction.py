from __future__ import annotations

from dataclasses import dataclass
from typing import TypeAlias

from adb.server.lifecycle.control.backend import (
    AdbServerBackendAcquireBlocked,
    AdbServerBackendAcquireFailed,
    AdbServerBackendAcquireInProgress,
    AdbServerBackendAcquireSatisfied,
    AdbServerBackendAcquireSucceeded,
)
from adb.server.state import (
    AdbServerActivated,
    AdbServerActivationRejected,
    AdbServerActivationResult,
    AdbServerState,
)


AdbServerBackendAcquireUnavailable: TypeAlias = (
    AdbServerBackendAcquireInProgress
    | AdbServerBackendAcquireBlocked
    | AdbServerBackendAcquireFailed
)
AdbServerBackendAcquireUsable: TypeAlias = (
    AdbServerBackendAcquireSucceeded | AdbServerBackendAcquireSatisfied
)


@dataclass(frozen=True, slots=True)
class AdbServerProvisionStateConflict:
    """Provisioning could not begin from the observed authoritative server state."""

    state: AdbServerState

    def __post_init__(self) -> None:
        if not isinstance(self.state, AdbServerState):
            raise TypeError("state must be AdbServerState")


@dataclass(frozen=True, slots=True)
class AdbServerProvisionAcquireStopped:
    """Provisioning stopped at backend acquisition while preserving its raw evidence."""

    acquire: AdbServerBackendAcquireUnavailable

    def __post_init__(self) -> None:
        if not isinstance(
            self.acquire,
            (
                AdbServerBackendAcquireInProgress,
                AdbServerBackendAcquireBlocked,
                AdbServerBackendAcquireFailed,
            ),
        ):
            raise TypeError("acquire must be an unavailable ADB server backend acquire result")


@dataclass(frozen=True, slots=True)
class AdbServerProvisionActivationAttempted:
    """A usable backend attachment was acquired and authoritative activation was attempted."""

    acquire: AdbServerBackendAcquireUsable
    activation: AdbServerActivationResult

    def __post_init__(self) -> None:
        if not isinstance(
            self.acquire,
            (AdbServerBackendAcquireSucceeded, AdbServerBackendAcquireSatisfied),
        ):
            raise TypeError("acquire must be a usable ADB server backend acquire result")
        if not isinstance(
            self.activation,
            (AdbServerActivated, AdbServerActivationRejected),
        ):
            raise TypeError("activation must be AdbServerActivationResult")


AdbServerProvisionTransactionResult: TypeAlias = (
    AdbServerProvisionStateConflict
    | AdbServerProvisionAcquireStopped
    | AdbServerProvisionActivationAttempted
)


__all__ = [
    "AdbServerBackendAcquireUnavailable",
    "AdbServerBackendAcquireUsable",
    "AdbServerProvisionAcquireStopped",
    "AdbServerProvisionActivationAttempted",
    "AdbServerProvisionStateConflict",
    "AdbServerProvisionTransactionResult",
]
