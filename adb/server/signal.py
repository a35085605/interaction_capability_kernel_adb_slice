from __future__ import annotations

from dataclasses import dataclass
from typing import TypeAlias

from adb.server.lifecycle.command import AdbServerStart, AdbServerStop
from adb.server.lifecycle.ensure import (
    AdbServerEnsureResult,
    AdbServerProbeResult,
)
from native_attempt import NativeAttemptResult


AdbServerCommandOperation: TypeAlias = AdbServerStart | AdbServerStop


@dataclass(frozen=True, slots=True)
class AdbServerCommandCompleted:
    """Signal carrying the result of one atomic ADB server command attempt."""

    operation: AdbServerCommandOperation
    result: NativeAttemptResult

    def __post_init__(self) -> None:
        if not isinstance(self.operation, (AdbServerStart, AdbServerStop)):
            raise TypeError("operation must be an ADB server command operation")
        if not isinstance(self.result, NativeAttemptResult):
            raise TypeError("result must be NativeAttemptResult")


@dataclass(frozen=True, slots=True)
class AdbServerProbeCompleted:
    """Signal carrying evidence from one fresh ADB server probe."""

    probe: AdbServerProbeResult

    def __post_init__(self) -> None:
        if not isinstance(self.probe, AdbServerProbeResult):
            raise TypeError("probe must be AdbServerProbeResult")


@dataclass(frozen=True, slots=True)
class AdbServerEnsureCompleted:
    """Signal carrying terminal evidence from one ADB server ensure operation."""

    result: AdbServerEnsureResult

    def __post_init__(self) -> None:
        if not isinstance(self.result, AdbServerEnsureResult):
            raise TypeError("result must be AdbServerEnsureResult")


AdbServerSignal: TypeAlias = (
    AdbServerCommandCompleted
    | AdbServerProbeCompleted
    | AdbServerEnsureCompleted
)


__all__ = [
    "AdbServerCommandCompleted",
    "AdbServerCommandOperation",
    "AdbServerEnsureCompleted",
    "AdbServerProbeCompleted",
    "AdbServerSignal",
]
