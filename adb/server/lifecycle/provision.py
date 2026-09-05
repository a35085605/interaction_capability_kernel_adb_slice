from __future__ import annotations

from dataclasses import dataclass
from typing import TypeAlias

from adb.server.lifecycle.backend import (
    AdbServerBackendAcquireAchieved,
    AdbServerBackendAcquireBlocked,
    AdbServerBackendAcquireFailed,
    AdbServerBackendAcquireInProgress,
    AdbServerBackendAcquirePreexisting,
)
from adb.server.lifecycle.coordinator import (
    AdbServerAlreadyActive,
    AdbServerProvisionResult,
)
from adb.server.lifecycle.errors import AdbServerLifecycleConsistencyError
from adb.server.state import (
    AdbServerActivated,
    AdbServerActivationStateConflict,
)


@dataclass(frozen=True, slots=True)
class AdbServerProvisionActivated:
    """Validated provision outcome that committed a newly authoritative server."""

    acquisition: AdbServerBackendAcquireAchieved
    activation: AdbServerActivated

    def __post_init__(self) -> None:
        if not isinstance(self.acquisition, AdbServerBackendAcquireAchieved):
            raise TypeError("acquisition must be AdbServerBackendAcquireAchieved")
        if not isinstance(self.activation, AdbServerActivated):
            raise TypeError("activation must be AdbServerActivated")
        if self.acquisition.endpoint != self.activation.state.endpoint:
            raise AdbServerLifecycleConsistencyError(
                "activated ADB server endpoint does not match backend acquisition endpoint"
            )


@dataclass(frozen=True, slots=True)
class AdbServerProvisionActivationConflict:
    """Validated provision outcome whose newly achieved effect lost the activation fence."""

    acquisition: AdbServerBackendAcquireAchieved
    activation: AdbServerActivationStateConflict

    def __post_init__(self) -> None:
        if not isinstance(self.acquisition, AdbServerBackendAcquireAchieved):
            raise TypeError("acquisition must be AdbServerBackendAcquireAchieved")
        if not isinstance(self.activation, AdbServerActivationStateConflict):
            raise TypeError("activation must be AdbServerActivationStateConflict")


AdbServerProvisionOutcome: TypeAlias = (
    AdbServerAlreadyActive
    | AdbServerBackendAcquirePreexisting
    | AdbServerBackendAcquireInProgress
    | AdbServerBackendAcquireBlocked
    | AdbServerBackendAcquireFailed
    | AdbServerProvisionActivated
    | AdbServerProvisionActivationConflict
)


def classify_provision_result(
    evidence: AdbServerProvisionResult,
) -> AdbServerProvisionOutcome:
    """Validate raw provision evidence and reduce it to one canonical lifecycle outcome."""

    if not isinstance(evidence, tuple) or not evidence:
        raise TypeError("server lifecycle provision() must return non-empty ordered evidence")

    if len(evidence) == 1:
        first = evidence[0]
        if isinstance(first, AdbServerAlreadyActive):
            return first
        if isinstance(
            first,
            (
                AdbServerBackendAcquirePreexisting,
                AdbServerBackendAcquireInProgress,
                AdbServerBackendAcquireBlocked,
                AdbServerBackendAcquireFailed,
            ),
        ):
            return first
        if isinstance(first, AdbServerBackendAcquireAchieved):
            raise TypeError(
                "newly achieved backend acquire evidence must be followed by activation evidence"
            )
        raise TypeError(
            "provision evidence must begin with already-active or backend acquire evidence"
        )

    if len(evidence) == 2:
        acquisition, activation = evidence
        if not isinstance(acquisition, AdbServerBackendAcquireAchieved):
            if isinstance(
                acquisition,
                (
                    AdbServerAlreadyActive,
                    AdbServerBackendAcquirePreexisting,
                    AdbServerBackendAcquireInProgress,
                    AdbServerBackendAcquireBlocked,
                    AdbServerBackendAcquireFailed,
                ),
            ):
                raise TypeError("terminal provision evidence must not be followed by more evidence")
            raise TypeError(
                "provision evidence must begin with already-active or backend acquire evidence"
            )

        if isinstance(activation, AdbServerActivated):
            return AdbServerProvisionActivated(acquisition, activation)
        if isinstance(activation, AdbServerActivationStateConflict):
            return AdbServerProvisionActivationConflict(acquisition, activation)
        raise TypeError(
            "newly achieved backend acquire evidence must be followed by activation evidence"
        )

    raise TypeError("server lifecycle provision() returned unsupported evidence shape")


__all__ = [
    "AdbServerProvisionActivated",
    "AdbServerProvisionActivationConflict",
    "AdbServerProvisionOutcome",
    "classify_provision_result",
]
