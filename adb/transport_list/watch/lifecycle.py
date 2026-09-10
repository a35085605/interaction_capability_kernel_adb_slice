from __future__ import annotations

from typing import Protocol, TypeAlias, runtime_checkable

from adb._lifecycle import (
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
from adb.transport_list.watch.access import AdbTransportListWatchAccess
from adb.transport_list.watch.failure import AdbTransportListWatchFailure
from adb.transport_list.watch.generation import (
    AdbTransportListWatchGeneration,
    AdbTransportListWatchGenerationIssuer,
)
from adb.transport_list.watch.state import AdbTransportListWatchStateView
from adb.transport_list.watch.stream import AdbTransportListWatchStream


AdbTransportListWatchAcquireOutcome: TypeAlias = (
    AcquireCommitted[
        AdbTransportListWatchGeneration,
        AdbTransportListWatchAccess,
        AdbTransportListWatchStream,
    ]
    | AcquireExisting[
        AdbTransportListWatchGeneration,
        AdbTransportListWatchAccess,
        AdbTransportListWatchStream,
    ]
    | GenerationMismatch[AdbTransportListWatchGeneration]
    | AcquireBlocked
    | AcquireFailed[AdbTransportListWatchFailure]
    | AcquireSuperseded[AdbTransportListWatchGeneration]
)

AdbTransportListWatchReleaseOutcome: TypeAlias = (
    ReleaseAcquisitionRevoked[AdbTransportListWatchGeneration]
    | ReleaseAccessDetached[
        AdbTransportListWatchGeneration,
        AdbTransportListWatchAccess,
    ]
    | ReleaseAccessMismatch[AdbTransportListWatchAccess]
    | ReleaseInactive
    | GenerationMismatch[AdbTransportListWatchGeneration]
)


@runtime_checkable
class AdbTransportListWatchLifecycle(AdbTransportListWatchStateView, Protocol):
    """Sole authority for one runtime-scoped transport-list watch generation.

    ``read()`` and successful acquire outcomes expose the same atomic generation/access/stream
    snapshot. Acquire and release are generation-fenced, and release additionally matches access.
    Physical watch-resource ownership and cleanup remain lifecycle implementation details.
    """

    def acquire(
        self,
        expected_generation: AdbTransportListWatchGeneration,
        access: AdbTransportListWatchAccess,
    ) -> AdbTransportListWatchAcquireOutcome:
        """Acquire ``access`` only if ``expected_generation`` is still current."""
        ...

    def release(
        self,
        expected_generation: AdbTransportListWatchGeneration,
        access: AdbTransportListWatchAccess,
    ) -> AdbTransportListWatchReleaseOutcome:
        """Release matching generation/access authority and advance generation on revocation."""
        ...


class AdbTransportListWatchLifecycleFactory(Protocol):
    """Construct one runtime-scoped transport-list watch lifecycle."""

    def __call__(
        self,
        generation_issuer: AdbTransportListWatchGenerationIssuer,
    ) -> AdbTransportListWatchLifecycle:
        ...


__all__ = [
    "AcquireBlocked",
    "AcquireCommitted",
    "AcquireExisting",
    "AcquireFailed",
    "AcquireSuperseded",
    "AdbTransportListWatchAccess",
    "AdbTransportListWatchAcquireOutcome",
    "AdbTransportListWatchLifecycle",
    "AdbTransportListWatchLifecycleFactory",
    "AdbTransportListWatchReleaseOutcome",
    "GenerationMismatch",
    "ReleaseAccessDetached",
    "ReleaseAccessMismatch",
    "ReleaseAcquisitionRevoked",
    "ReleaseInactive",
]
