from __future__ import annotations

from typing import Protocol, TypeAlias, runtime_checkable

from lifecycle.capability.result import (
    AcquireResult,
    LifecycleResult,
    RecoveryResult,
    ReleaseResult,
)
from adb.server.capability import AdbServerCapability
from adb.server.generation import AdbServerGeneration, AdbServerGenerationIssuer
from adb.server.request import AdbServerRequest
from adb.server.snapshot import AdbServerSnapshotReader


AdbServerLifecycleResult: TypeAlias = LifecycleResult[
    AdbServerGeneration, AdbServerRequest, AdbServerCapability
]
AdbServerAcquireResult: TypeAlias = AcquireResult[
    AdbServerGeneration, AdbServerRequest, AdbServerCapability
]
AdbServerReleaseResult: TypeAlias = ReleaseResult[
    AdbServerGeneration, AdbServerRequest, AdbServerCapability
]
AdbServerRecoveryResult: TypeAlias = RecoveryResult[
    AdbServerGeneration, AdbServerRequest, AdbServerCapability
]


@runtime_checkable
class AdbServerLifecycle(AdbServerSnapshotReader, Protocol):
    def acquire(
        self, expected_generation: AdbServerGeneration, request: AdbServerRequest
    ) -> AdbServerAcquireResult: ...

    def release(
        self, expected_generation: AdbServerGeneration, request: AdbServerRequest
    ) -> AdbServerReleaseResult: ...

    def recover(
        self, expected_generation: AdbServerGeneration, request: AdbServerRequest
    ) -> AdbServerRecoveryResult: ...


class AdbServerLifecycleFactory(Protocol):
    def __call__(
        self, generation_issuer: AdbServerGenerationIssuer
    ) -> AdbServerLifecycle: ...


__all__ = [
    "AdbServerAcquireResult",
    "AdbServerLifecycle",
    "AdbServerLifecycleFactory",
    "AdbServerLifecycleResult",
    "AdbServerRecoveryResult",
    "AdbServerReleaseResult",
]
