from __future__ import annotations

from typing import Protocol, TypeAlias, runtime_checkable

from networking import TcpAddress

from adb._lifecycle import (
    AcquireBlocked,
    AcquireCommitted,
    AcquireExisting,
    AcquireFailed,
    AcquireSuperseded,
    ReleaseAccessDetached,
    ReleaseAcquisitionRevoked,
    ReleaseGenerationMismatch,
    ReleaseInactive,
)
from adb.server.access import AdbServerAccess
from adb.server.failure import AdbServerLaunchFailure
from adb.server.errors import (
    AdbServerLifecycleConsistencyError,
    AdbServerLifecycleError,
)
from adb.server.generation import AdbServerGeneration, AdbServerGenerationIssuer
from adb.server.state import AdbServerStateView


AdbServerAcquireOutcome: TypeAlias = (
    AcquireCommitted[AdbServerGeneration, AdbServerAccess]
    | AcquireExisting[AdbServerGeneration, AdbServerAccess]
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

    ``read()`` exposes an atomic generation/server-capability snapshot for data-plane consumers.
    Acquire/release outcomes retain public server-address metadata for control-plane coordination.
    Pending work, cleanup diagnostics, physical resources, and resource claims remain lifecycle
    implementation details.
    """

    def acquire(
        self,
        server_address: TcpAddress,
    ) -> AdbServerAcquireOutcome:
        """Attempt to establish usable ADB server access at the requested server address."""
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
    "AdbServerLifecycleConsistencyError",
    "AdbServerLifecycleError",
    "AdbServerReleaseOutcome",
    "ReleaseAccessDetached",
    "ReleaseAcquisitionRevoked",
    "ReleaseGenerationMismatch",
    "ReleaseInactive",
]
