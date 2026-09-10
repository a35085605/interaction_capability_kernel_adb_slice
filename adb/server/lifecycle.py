from __future__ import annotations

from typing import Protocol, TypeAlias, runtime_checkable

from networking import TcpAddress

from adb._lifecycle import (
    AcquireBlocked,
    AcquireCommitted,
    AcquireExisting,
    AcquireFailed,
    AcquireGenerationMismatch,
    AcquireSuperseded,
    ReleaseAccessDetached,
    ReleaseAccessMismatch,
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
    AcquireCommitted[AdbServerGeneration, AdbServerAccess, TcpAddress]
    | AcquireExisting[AdbServerGeneration, AdbServerAccess, TcpAddress]
    | AcquireGenerationMismatch[AdbServerGeneration, AdbServerAccess, TcpAddress]
    | AcquireBlocked
    | AcquireFailed[AdbServerLaunchFailure]
    | AcquireSuperseded[AdbServerGeneration]
)

AdbServerReleaseOutcome: TypeAlias = (
    ReleaseAcquisitionRevoked[AdbServerGeneration, AdbServerAccess]
    | ReleaseAccessDetached[AdbServerGeneration, AdbServerAccess]
    | ReleaseAccessMismatch[AdbServerGeneration, AdbServerAccess]
    | ReleaseInactive[AdbServerGeneration]
    | ReleaseGenerationMismatch[AdbServerGeneration, AdbServerAccess]
)


@runtime_checkable
class AdbServerLifecycle(AdbServerStateView, Protocol):
    """Sole authority for one runtime-scoped ADB server generation.

    ``read()`` and successful acquire outcomes expose the same atomic generation/access/capability
    snapshot. Acquire and release are both fenced by the caller's expected generation. Release also
    requires the requested access to match before logical revocation advances authority.
    """

    def acquire(
        self,
        expected_generation: AdbServerGeneration,
        access: AdbServerAccess,
    ) -> AdbServerAcquireOutcome:
        """Acquire ``access`` only if ``expected_generation`` is still current."""
        ...

    def release(
        self,
        expected_generation: AdbServerGeneration,
        access: AdbServerAccess,
    ) -> AdbServerReleaseOutcome:
        """Release matching generation/access authority and advance generation on revocation."""
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
    "AcquireGenerationMismatch",
    "AcquireSuperseded",
    "AdbServerAccess",
    "AdbServerAcquireOutcome",
    "AdbServerLifecycle",
    "AdbServerLifecycleFactory",
    "AdbServerLifecycleConsistencyError",
    "AdbServerLifecycleError",
    "AdbServerReleaseOutcome",
    "ReleaseAccessDetached",
    "ReleaseAccessMismatch",
    "ReleaseAcquisitionRevoked",
    "ReleaseGenerationMismatch",
    "ReleaseInactive",
]
