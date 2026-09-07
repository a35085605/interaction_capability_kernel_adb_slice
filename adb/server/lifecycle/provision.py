from __future__ import annotations

from dataclasses import dataclass
from typing import TypeAlias

from adb.server.lifecycle.backend import (
    AdbServerBackendAcquired,
    AdbServerBackendAcquireDeferred,
    AdbServerBackendAcquireFailed,
    AdbServerBackendAcquireRevoked,
)
from adb.server.lifecycle.coordinator import (
    AdbServerAlreadyActive,
    AdbServerProvisionResult,
)
from adb.server.lifecycle.errors import AdbServerLifecycleConsistencyError
from adb.server.lifecycle.events import AdbServerActivated


@dataclass(frozen=True, slots=True)
class AdbServerProvisionActivated:
    """Validated provision outcome for one newly authoritative backend acquisition."""

    acquisition: AdbServerBackendAcquired
    activation: AdbServerActivated

    def __post_init__(self) -> None:
        if not isinstance(self.acquisition, AdbServerBackendAcquired):
            raise TypeError("acquisition must be AdbServerBackendAcquired")
        if not isinstance(self.activation, AdbServerActivated):
            raise TypeError("activation must be AdbServerActivated")
        if self.acquisition.generation != self.activation.generation:
            raise AdbServerLifecycleConsistencyError(
                "activated ADB server does not match backend acquisition"
            )


AdbServerProvisionOutcome: TypeAlias = (
    AdbServerAlreadyActive
    | AdbServerBackendAcquireDeferred
    | AdbServerBackendAcquireFailed
    | AdbServerBackendAcquireRevoked
    | AdbServerProvisionActivated
)


def classify_provision_result(
    evidence: AdbServerProvisionResult,
) -> AdbServerProvisionOutcome:
    """Validate raw provision evidence and reduce it to one canonical lifecycle outcome."""

    if not isinstance(evidence, tuple) or not evidence:
        raise TypeError("server lifecycle provision() must return non-empty ordered evidence")

    if len(evidence) == 1:
        first = evidence[0]
        if isinstance(
            first,
            (
                AdbServerAlreadyActive,
                AdbServerBackendAcquireDeferred,
                AdbServerBackendAcquireFailed,
                AdbServerBackendAcquireRevoked,
            ),
        ):
            return first
        if isinstance(first, AdbServerBackendAcquired):
            raise TypeError(
                "newly acquired backend evidence must be followed by activation evidence"
            )
        raise TypeError("server lifecycle provision() returned unsupported terminal evidence")

    if len(evidence) == 2:
        acquisition, activation = evidence
        if not isinstance(acquisition, AdbServerBackendAcquired):
            raise TypeError("provision evidence must begin with backend acquire evidence")
        if not isinstance(activation, AdbServerActivated):
            raise TypeError(
                "newly acquired backend evidence must be followed by activation evidence"
            )
        return AdbServerProvisionActivated(acquisition, activation)

    raise TypeError("server lifecycle provision() returned unsupported evidence shape")


__all__ = [
    "AdbServerProvisionActivated",
    "AdbServerProvisionOutcome",
    "classify_provision_result",
]
