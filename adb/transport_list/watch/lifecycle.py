from __future__ import annotations

from typing import Protocol, TypeAlias, runtime_checkable

from _lifecycle_new.capability.result import (
    AcquireAlreadyActive,
    AcquireFailed,
    AcquireReleaseRequired,
    AcquireRequestMismatch,
    AcquireResult,
    AcquireSucceeded,
    GenerationMismatch,
    LifecycleBusy,
    ReleaseAlreadyIdle,
    ReleaseFailed,
    ReleaseRequestMismatch,
    ReleaseResult,
    ReleaseSucceeded,
)
from adb.transport_list.watch.generation import (
    AdbTransportListWatchGeneration,
    AdbTransportListWatchGenerationIssuer,
)
from adb.transport_list.watch.request import AdbTransportListWatchRequest
from adb.transport_list.watch.state import AdbTransportListWatchStateView
from adb.transport_list.watch.stream import AdbTransportListWatchStream


AdbTransportListWatchAcquireAlreadyActive = AcquireAlreadyActive
AdbTransportListWatchAcquireFailed = AcquireFailed
AdbTransportListWatchAcquireReleaseRequired = AcquireReleaseRequired
AdbTransportListWatchAcquireRequestMismatch = AcquireRequestMismatch
AdbTransportListWatchAcquireSucceeded = AcquireSucceeded
AdbTransportListWatchGenerationMismatch = GenerationMismatch
AdbTransportListWatchLifecycleBusy = LifecycleBusy
AdbTransportListWatchReleaseAlreadyIdle = ReleaseAlreadyIdle
AdbTransportListWatchReleaseFailed = ReleaseFailed
AdbTransportListWatchReleaseRequestMismatch = ReleaseRequestMismatch
AdbTransportListWatchReleaseSucceeded = ReleaseSucceeded

AdbTransportListWatchAcquireResult: TypeAlias = AcquireResult[
    AdbTransportListWatchGeneration,
    AdbTransportListWatchRequest,
    AdbTransportListWatchStream,
]

AdbTransportListWatchReleaseResult: TypeAlias = ReleaseResult[
    AdbTransportListWatchGeneration,
    AdbTransportListWatchRequest,
    AdbTransportListWatchStream,
]


@runtime_checkable
class AdbTransportListWatchLifecycle(AdbTransportListWatchStateView, Protocol):
    """Acquire and release one generation-scoped transport-list watch capability."""

    def acquire(
        self,
        expected_generation: AdbTransportListWatchGeneration,
        request: AdbTransportListWatchRequest,
    ) -> AdbTransportListWatchAcquireResult: ...

    def release(
        self,
        expected_generation: AdbTransportListWatchGeneration,
        request: AdbTransportListWatchRequest,
    ) -> AdbTransportListWatchReleaseResult: ...


class AdbTransportListWatchLifecycleFactory(Protocol):
    """Construct one runtime-scoped transport-list watch lifecycle."""

    def __call__(
        self,
        generation_issuer: AdbTransportListWatchGenerationIssuer,
    ) -> AdbTransportListWatchLifecycle: ...


__all__ = [
    "AdbTransportListWatchAcquireAlreadyActive",
    "AdbTransportListWatchAcquireFailed",
    "AdbTransportListWatchAcquireReleaseRequired",
    "AdbTransportListWatchAcquireRequestMismatch",
    "AdbTransportListWatchAcquireResult",
    "AdbTransportListWatchAcquireSucceeded",
    "AdbTransportListWatchGenerationMismatch",
    "AdbTransportListWatchLifecycle",
    "AdbTransportListWatchLifecycleBusy",
    "AdbTransportListWatchLifecycleFactory",
    "AdbTransportListWatchReleaseAlreadyIdle",
    "AdbTransportListWatchReleaseFailed",
    "AdbTransportListWatchReleaseRequestMismatch",
    "AdbTransportListWatchReleaseResult",
    "AdbTransportListWatchReleaseSucceeded",
]
