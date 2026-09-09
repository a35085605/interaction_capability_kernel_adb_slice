from __future__ import annotations

from typing import Protocol, TypeAlias, runtime_checkable

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
from adb.server.endpoint import AdbServerEndpoint
from adb.server.failure import AdbServerLaunchFailure
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

    ``read()`` exposes public endpoint state only. Pending work, cleanup diagnostics, physical
    resources, and resource claims remain lifecycle implementation details.
    """

    def acquire(
        self,
        endpoint: AdbServerEndpoint,
    ) -> AdbServerAcquireOutcome:
        """Attempt to establish usable ADB server access at the requested endpoint."""
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
