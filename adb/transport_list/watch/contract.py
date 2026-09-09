from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, TypeAlias, runtime_checkable

from networking import TcpAddress

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
from adb.transport_list.watch.failure import AdbTransportListWatchFailure
from adb.transport_list.watch.generation import (
    AdbTransportListWatchGeneration,
    AdbTransportListWatchGenerationIssuer,
)
from adb.transport_list.watch.state import AdbTransportListWatchStateView


@dataclass(frozen=True, slots=True)
class AdbTransportListWatchAccess(EndpointAccess[AdbTransportListWatchGeneration]):
    """Watch lifecycle evidence: generation plus endpoint, without the private stream handle."""

    def __post_init__(self) -> None:
        EndpointAccess.__post_init__(self)
        if not isinstance(self.generation, AdbTransportListWatchGeneration):
            raise TypeError("generation must be AdbTransportListWatchGeneration")


AdbTransportListWatchAcquireOutcome: TypeAlias = (
    AcquireCommitted[AdbTransportListWatchAccess]
    | AcquireExisting[AdbTransportListWatchAccess]
    | AcquireBlocked
    | AcquireFailed[AdbTransportListWatchFailure]
    | AcquireSuperseded[AdbTransportListWatchGeneration]
)

AdbTransportListWatchReleaseOutcome: TypeAlias = (
    ReleaseAcquisitionRevoked[AdbTransportListWatchGeneration]
    | ReleaseAccessDetached[AdbTransportListWatchGeneration, AdbTransportListWatchAccess]
    | ReleaseInactive[AdbTransportListWatchGeneration]
    | ReleaseGenerationMismatch[
        AdbTransportListWatchGeneration, AdbTransportListWatchAccess
    ]
)


@runtime_checkable
class AdbTransportListWatchLifecycle(AdbTransportListWatchStateView, Protocol):
    """Sole authority for one runtime-scoped transport-list watch generation.

    The public access value contains endpoint metadata only. The lifecycle-private stream/handle and
    cleanup ownership remain separate from this contract.
    """

    def acquire(self, endpoint: TcpAddress) -> AdbTransportListWatchAcquireOutcome:
        """Attempt to establish one fully usable watch within the current generation."""
        ...

    def release(
        self,
        expected: AdbTransportListWatchGeneration,
    ) -> AdbTransportListWatchReleaseOutcome:
        """Release matching authority and advance generation at logical revocation."""
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
    "ReleaseAccessDetached",
    "ReleaseAcquisitionRevoked",
    "ReleaseGenerationMismatch",
    "ReleaseInactive",
]
