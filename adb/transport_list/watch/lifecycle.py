from __future__ import annotations

from typing import Protocol, TypeAlias, runtime_checkable

from lifecycle.capability.result import AcquireResult, LifecycleResult, RecoveryResult, ReleaseResult
from adb.transport_list.watch.generation import (
    AdbTransportListWatchGeneration,
    AdbTransportListWatchGenerationIssuer,
)
from adb.transport_list.watch.request import AdbTransportListWatchRequest
from adb.transport_list.watch.snapshot import AdbTransportListWatchSnapshotReader
from adb.transport_list.watch.stream import AdbTransportListWatchStream


AdbTransportListWatchLifecycleResult: TypeAlias = LifecycleResult[
    AdbTransportListWatchGeneration,
    AdbTransportListWatchRequest,
    AdbTransportListWatchStream,
]
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
AdbTransportListWatchRecoveryResult: TypeAlias = RecoveryResult[
    AdbTransportListWatchGeneration,
    AdbTransportListWatchRequest,
    AdbTransportListWatchStream,
]


@runtime_checkable
class AdbTransportListWatchLifecycle(AdbTransportListWatchSnapshotReader, Protocol):
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

    def recover(
        self,
        expected_generation: AdbTransportListWatchGeneration,
        request: AdbTransportListWatchRequest,
    ) -> AdbTransportListWatchRecoveryResult: ...


class AdbTransportListWatchLifecycleFactory(Protocol):
    def __call__(
        self,
        generation_issuer: AdbTransportListWatchGenerationIssuer,
    ) -> AdbTransportListWatchLifecycle: ...


__all__ = [
    "AdbTransportListWatchAcquireResult",
    "AdbTransportListWatchLifecycle",
    "AdbTransportListWatchLifecycleFactory",
    "AdbTransportListWatchLifecycleResult",
    "AdbTransportListWatchRecoveryResult",
    "AdbTransportListWatchReleaseResult",
]
