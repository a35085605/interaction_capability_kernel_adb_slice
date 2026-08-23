from __future__ import annotations

from dataclasses import dataclass
from typing import TypeAlias

from adb.transport.lifecycle.control.port import (
    AdbDeviceSideReconnect,
    AdbOfflineTransportsReconnect,
    AdbTcpConnect,
    AdbTcpDisconnect,
    AdbTransportCommandOperation,
    AdbTransportReconnect,
)
from adb.transport.lifecycle.ensure import AdbTransportEnsureResult
from native_attempt import NativeAttemptResult


@dataclass(frozen=True, slots=True)
class AdbTransportCommandCompleted:
    """Signal carrying one ADB transport control attempt result."""

    operation: AdbTransportCommandOperation
    result: NativeAttemptResult

    def __post_init__(self) -> None:
        if not isinstance(
            self.operation,
            (
                AdbTcpConnect,
                AdbTcpDisconnect,
                AdbTransportReconnect,
                AdbDeviceSideReconnect,
                AdbOfflineTransportsReconnect,
            ),
        ):
            raise TypeError("operation must be an ADB transport command operation")
        if not isinstance(self.result, NativeAttemptResult):
            raise TypeError("result must be NativeAttemptResult")


@dataclass(frozen=True, slots=True)
class AdbTransportEnsureCompleted:
    """Signal carrying terminal evidence from one transport-readiness ensure operation."""

    result: AdbTransportEnsureResult

    def __post_init__(self) -> None:
        if not isinstance(self.result, AdbTransportEnsureResult):
            raise TypeError("result must be AdbTransportEnsureResult")


AdbTransportLifecycleSignal: TypeAlias = (
    AdbTransportCommandCompleted | AdbTransportEnsureCompleted
)


__all__ = [
    "AdbTransportCommandCompleted",
    "AdbTransportCommandOperation",
    "AdbTransportEnsureCompleted",
    "AdbTransportLifecycleSignal",
]
