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
from adb.server.capability import AdbServerCapability
from adb.server.generation import AdbServerGeneration, AdbServerGenerationIssuer
from adb.server.request import AdbServerRequest
from adb.server.snapshot import AdbServerSnapshotReader


AdbServerAcquireAlreadyActive = AcquireAlreadyActive
AdbServerAcquireFailed = AcquireFailed
AdbServerAcquireReleaseRequired = AcquireReleaseRequired
AdbServerAcquireRequestMismatch = AcquireRequestMismatch
AdbServerAcquireSucceeded = AcquireSucceeded
AdbServerGenerationMismatch = GenerationMismatch
AdbServerLifecycleBusy = LifecycleBusy
AdbServerReleaseAlreadyIdle = ReleaseAlreadyIdle
AdbServerReleaseFailed = ReleaseFailed
AdbServerReleaseRequestMismatch = ReleaseRequestMismatch
AdbServerReleaseSucceeded = ReleaseSucceeded

AdbServerAcquireResult: TypeAlias = AcquireResult[
    AdbServerGeneration,
    AdbServerRequest,
    AdbServerCapability,
]

AdbServerReleaseResult: TypeAlias = ReleaseResult[
    AdbServerGeneration,
    AdbServerRequest,
    AdbServerCapability,
]


@runtime_checkable
class AdbServerLifecycle(AdbServerSnapshotReader, Protocol):
    """Acquire and release one generation-scoped ADB server capability."""

    def acquire(
        self,
        expected_generation: AdbServerGeneration,
        request: AdbServerRequest,
    ) -> AdbServerAcquireResult: ...

    def release(
        self,
        expected_generation: AdbServerGeneration,
        request: AdbServerRequest,
    ) -> AdbServerReleaseResult: ...


class AdbServerLifecycleFactory(Protocol):
    """Build an ADB server lifecycle using the supplied generation issuer."""

    def __call__(
        self,
        generation_issuer: AdbServerGenerationIssuer,
    ) -> AdbServerLifecycle: ...


__all__ = [
    "AdbServerAcquireAlreadyActive",
    "AdbServerAcquireFailed",
    "AdbServerAcquireReleaseRequired",
    "AdbServerAcquireRequestMismatch",
    "AdbServerAcquireResult",
    "AdbServerAcquireSucceeded",
    "AdbServerGenerationMismatch",
    "AdbServerLifecycle",
    "AdbServerLifecycleBusy",
    "AdbServerLifecycleFactory",
    "AdbServerReleaseAlreadyIdle",
    "AdbServerReleaseFailed",
    "AdbServerReleaseRequestMismatch",
    "AdbServerReleaseResult",
    "AdbServerReleaseSucceeded",
]
