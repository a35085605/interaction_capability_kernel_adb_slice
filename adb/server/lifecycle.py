from __future__ import annotations

from typing import Protocol, TypeAlias, runtime_checkable

from networking import TcpAddress

from _lifecycle_new.capability.result import (
    AcquireAlreadyActive as CapabilityAcquireAlreadyActive,
    AcquireFailed as CapabilityAcquireFailed,
    AcquireReleaseRequired as CapabilityAcquireReleaseRequired,
    AcquireRequestMismatch as CapabilityAcquireRequestMismatch,
    AcquireResult as CapabilityAcquireResult,
    AcquireSucceeded as CapabilityAcquireSucceeded,
    GenerationMismatch as CapabilityGenerationMismatch,
    LifecycleBusy as CapabilityLifecycleBusy,
    ReleaseAlreadyIdle as CapabilityReleaseAlreadyIdle,
    ReleaseFailed as CapabilityReleaseFailed,
    ReleaseRequestMismatch as CapabilityReleaseRequestMismatch,
    ReleaseResult as CapabilityReleaseResult,
    ReleaseSucceeded as CapabilityReleaseSucceeded,
)
from adb._lifecycle import (
    AcquireAccessMismatch,
    AcquireBlocked,
    AcquireCommitted,
    AcquireExisting,
    AcquireFailed,
    AcquireSuperseded,
    GenerationMismatch,
    ReleaseAccessDetached,
    ReleaseAccessMismatch,
    ReleaseAcquisitionRevoked,
    ReleaseInactive,
)
from adb.server.access import AdbServerAccess
from adb.server.failure import AdbServerLaunchFailure
from adb.server.generation import AdbServerGeneration, AdbServerGenerationIssuer
from adb.server.state import AdbServerLifecycleSnapshot, AdbServerStateView


# Legacy lifecycle outcomes retained until server supervision migrates.
AdbServerAcquireOutcome: TypeAlias = (
    AcquireCommitted[AdbServerGeneration, AdbServerAccess, TcpAddress]
    | AcquireExisting[AdbServerGeneration, AdbServerAccess, TcpAddress]
    | AcquireAccessMismatch[AdbServerAccess]
    | GenerationMismatch[AdbServerGeneration]
    | AcquireBlocked
    | AcquireFailed[AdbServerLaunchFailure]
    | AcquireSuperseded[AdbServerGeneration]
)

AdbServerReleaseOutcome: TypeAlias = (
    ReleaseAcquisitionRevoked[AdbServerGeneration]
    | ReleaseAccessDetached[AdbServerGeneration]
    | ReleaseAccessMismatch[AdbServerAccess]
    | ReleaseInactive
    | GenerationMismatch[AdbServerGeneration]
)

# Simplified lifecycle result variants used by the new coordinator path. Prefix
# them at the ADB-server boundary so legacy result names can coexist during migration.
AdbServerAcquireAlreadyActive = CapabilityAcquireAlreadyActive
AdbServerAcquireFailed = CapabilityAcquireFailed
AdbServerAcquireReleaseRequired = CapabilityAcquireReleaseRequired
AdbServerAcquireRequestMismatch = CapabilityAcquireRequestMismatch
AdbServerAcquireSucceeded = CapabilityAcquireSucceeded
AdbServerGenerationMismatch = CapabilityGenerationMismatch
AdbServerLifecycleBusy = CapabilityLifecycleBusy
AdbServerReleaseAlreadyIdle = CapabilityReleaseAlreadyIdle
AdbServerReleaseFailed = CapabilityReleaseFailed
AdbServerReleaseRequestMismatch = CapabilityReleaseRequestMismatch
AdbServerReleaseSucceeded = CapabilityReleaseSucceeded

# The legacy Outcome aliases above remain temporarily for server supervision.
AdbServerAcquireResult: TypeAlias = CapabilityAcquireResult[
    AdbServerGeneration,
    AdbServerAccess,
    TcpAddress,
]
AdbServerReleaseResult: TypeAlias = CapabilityReleaseResult[
    AdbServerGeneration,
    AdbServerAccess,
    TcpAddress,
]


class AdbServerCapabilityLifecycle(Protocol):
    """Simplified synchronous lifecycle contract for ADB server capability ownership.

    Unlike the legacy ``AdbServerLifecycle``, an in-flight acquisition is not
    revoked by release. Overlapping lifecycle operations report ``LifecycleBusy``;
    acquisition/release failures retain cleanup responsibility in the current
    generation until an explicit release succeeds.
    """

    def read(self) -> AdbServerLifecycleSnapshot:
        ...

    def acquire(
        self,
        expected_generation: AdbServerGeneration,
        access: AdbServerAccess,
    ) -> AdbServerAcquireResult:
        ...

    def release(
        self,
        expected_generation: AdbServerGeneration,
        access: AdbServerAccess,
    ) -> AdbServerReleaseResult:
        ...


@runtime_checkable
class AdbServerLifecycle(AdbServerStateView, Protocol):
    """Legacy ADB server lifecycle contract retained during staged migration.

    New integrations should target ``AdbServerCapabilityLifecycle`` instead.

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
    """Construct one runtime-scoped simplified ADB server lifecycle."""

    def __call__(
        self,
        generation_issuer: AdbServerGenerationIssuer,
    ) -> AdbServerCapabilityLifecycle:
        ...


__all__ = [
    "AcquireAccessMismatch",
    "AcquireBlocked",
    "AcquireCommitted",
    "AcquireExisting",
    "AcquireFailed",
    "AcquireSuperseded",
    "AdbServerAccess",
    "AdbServerAcquireAlreadyActive",
    "AdbServerAcquireFailed",
    "AdbServerAcquireReleaseRequired",
    "AdbServerAcquireRequestMismatch",
    "AdbServerAcquireSucceeded",
    "AdbServerAcquireOutcome",
    "AdbServerAcquireResult",
    "AdbServerCapabilityLifecycle",
    "AdbServerGenerationMismatch",
    "AdbServerLifecycle",
    "AdbServerLifecycleBusy",
    "AdbServerLifecycleFactory",
    "AdbServerReleaseAlreadyIdle",
    "AdbServerReleaseFailed",
    "AdbServerReleaseOutcome",
    "AdbServerReleaseRequestMismatch",
    "AdbServerReleaseSucceeded",
    "AdbServerReleaseResult",
    "GenerationMismatch",
    "ReleaseAccessDetached",
    "ReleaseAccessMismatch",
    "ReleaseAcquisitionRevoked",
    "ReleaseInactive",
]
