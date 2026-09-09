from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, TypeAlias, runtime_checkable

from adb._lifecycle import (
    AcquireBlocked,
    AcquireCommitted,
    AcquireExisting,
    AcquireFailed,
    AcquireSuperseded,
    EndpointAccess,
    ReleaseAccessDetached,
    ReleaseAcquisitionRevoked,
    ReleaseGenerationMismatch,
    ReleaseInactive,
)
from adb.server.endpoint import AdbServerEndpoint
from adb.server.failure import AdbServerLaunchFailure
from adb.server.generation import AdbServerGeneration, AdbServerGenerationIssuer
from adb.server.state import AdbServerStateView


@dataclass(frozen=True, slots=True)
class AdbServerAccess(EndpointAccess[AdbServerGeneration]):
    """Usable ADB server endpoint access retained by one server generation."""

    def __post_init__(self) -> None:
        EndpointAccess.__post_init__(self)
        if not isinstance(self.generation, AdbServerGeneration):
            raise TypeError("generation must be AdbServerGeneration")


AdbServerAcquireOutcome: TypeAlias = (
    AcquireCommitted[AdbServerAccess]
    | AcquireExisting[AdbServerAccess]
    | AcquireBlocked
    | AcquireFailed[AdbServerLaunchFailure]
    | AcquireSuperseded[AdbServerGeneration]
)

AdbServerReleaseOutcome: TypeAlias = (
    ReleaseAcquisitionRevoked[AdbServerGeneration]
    | ReleaseAccessDetached[AdbServerGeneration, AdbServerAccess]
    | ReleaseInactive[AdbServerGeneration]
    | ReleaseGenerationMismatch[AdbServerGeneration, AdbServerAccess]
)


@runtime_checkable
class AdbServerLifecycle(AdbServerStateView, Protocol):
    """Sole authority for one runtime-scoped ADB server generation.

    ``read()`` exposes public endpoint state only. Pending work, cleanup diagnostics, physical
    resources, and resource claims remain lifecycle implementation details.
    """

    def acquire(
        self,
        endpoint_constraint: AdbServerEndpoint | None = None,
    ) -> AdbServerAcquireOutcome:
        """Attempt to establish usable ADB server access within the current generation."""
        ...

    def release(self, expected: AdbServerGeneration) -> AdbServerReleaseOutcome:
        """Release matching authority and advance generation at logical revocation."""
        ...


class AdbServerLifecycleFactory(Protocol):
    """Construct one runtime-scoped ADB server lifecycle."""

    def __call__(
        self,
        generation_issuer: AdbServerGenerationIssuer,
    ) -> AdbServerLifecycle:
        ...


__all__ = [
    "AcquireBlocked",
    "AcquireCommitted",
    "AcquireExisting",
    "AcquireFailed",
    "AcquireSuperseded",
    "AdbServerAccess",
    "AdbServerAcquireOutcome",
    "AdbServerLifecycle",
    "AdbServerLifecycleFactory",
    "AdbServerReleaseOutcome",
    "ReleaseAccessDetached",
    "ReleaseAcquisitionRevoked",
    "ReleaseGenerationMismatch",
    "ReleaseInactive",
]
