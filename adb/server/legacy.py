"""Legacy ADB server lifecycle contracts retained only for staged supervision migration."""

from __future__ import annotations

from typing import Protocol, TypeAlias, runtime_checkable

from networking import TcpAddress

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
    Snapshot,
)
from adb.server.access import AdbServerAccess
from adb.server.failure import AdbServerLaunchFailure
from adb.server.generation import AdbServerGeneration


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

AdbServerState: TypeAlias = Snapshot[
    AdbServerGeneration,
    AdbServerAccess,
    TcpAddress,
]


@runtime_checkable
class LegacyAdbServerLifecycle(Protocol):
    """Legacy cancellable server lifecycle retained until supervision migrates."""

    def read(self) -> AdbServerState: ...

    def acquire(
        self,
        expected_generation: AdbServerGeneration,
        access: AdbServerAccess,
    ) -> AdbServerAcquireOutcome: ...

    def release(
        self,
        expected_generation: AdbServerGeneration,
        access: AdbServerAccess,
    ) -> AdbServerReleaseOutcome: ...


__all__ = [
    "AdbServerAcquireOutcome",
    "AdbServerReleaseOutcome",
    "AdbServerState",
    "LegacyAdbServerLifecycle",
]
