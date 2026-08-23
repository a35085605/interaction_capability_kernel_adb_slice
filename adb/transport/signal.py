from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import TypeAlias

from adb.server.endpoint import AdbServerEndpoint
from adb.server.identity import AdbServer
from adb.transport.inventory.model import AdbDevicesSnapshot
from adb.transport.lifecycle.command import (
    AdbDeviceSideReconnect,
    AdbOfflineTransportsReconnect,
    AdbTcpConnect,
    AdbTcpDisconnect,
    AdbTransportCommandOperation,
    AdbTransportReconnect,
)
from adb.transport.lifecycle.ensure import AdbTransportEnsureResult
from native_attempt import NativeAttemptResult


def _require_server(value: object) -> AdbServer:
    if not isinstance(value, AdbServer):
        raise TypeError("server must be AdbServer")
    return value


def _normalize_optional_text(value: object, *, field_name: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise TypeError(f"{field_name} must be a string or None")
    normalized = value.strip()
    if not normalized:
        raise ValueError(f"{field_name} cannot be empty")
    return normalized


@dataclass(frozen=True, slots=True)
class AdbTransportCommandCompleted:
    """Signal carrying one ADB transport command result."""

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


class AdbDevicesTrackingFailure(str, Enum):
    """Reason a transport-inventory tracker terminated abnormally."""

    SERVER_CONNECTION = "server_connection"
    SERVICE = "service"
    PROTOCOL = "protocol"


@dataclass(frozen=True, slots=True)
class AdbDevicesTrackingStarted:
    """Signal that the current tracker entered stream mode for one server lifetime."""

    server: AdbServer

    def __post_init__(self) -> None:
        _require_server(self.server)

    @property
    def endpoint(self) -> AdbServerEndpoint:
        return self.server.endpoint

    @property
    def epoch(self) -> int:
        return self.server.epoch


@dataclass(frozen=True, slots=True)
class AdbDevicesTrackingStopped:
    """Signal that one server-bound tracker ended without implying transport disappearance."""

    server: AdbServer

    def __post_init__(self) -> None:
        _require_server(self.server)

    @property
    def endpoint(self) -> AdbServerEndpoint:
        return self.server.endpoint

    @property
    def epoch(self) -> int:
        return self.server.epoch


@dataclass(frozen=True, slots=True)
class AdbDevicesTrackingFailed:
    """Signal that the current tracker failed without synthesizing server state."""

    server: AdbServer
    failure: AdbDevicesTrackingFailure
    diagnostic: str | None = None

    def __post_init__(self) -> None:
        _require_server(self.server)
        if not isinstance(self.failure, AdbDevicesTrackingFailure):
            raise TypeError("failure must be AdbDevicesTrackingFailure")
        object.__setattr__(
            self,
            "diagnostic",
            _normalize_optional_text(
                self.diagnostic,
                field_name="ADB transport-inventory tracking diagnostic",
            ),
        )

    @property
    def endpoint(self) -> AdbServerEndpoint:
        return self.server.endpoint

    @property
    def epoch(self) -> int:
        return self.server.epoch


@dataclass(frozen=True, slots=True)
class AdbDevicesSnapshotObserved:
    """Signal carrying one complete snapshot emitted by the current tracker."""

    server: AdbServer
    snapshot: AdbDevicesSnapshot

    def __post_init__(self) -> None:
        _require_server(self.server)
        if not isinstance(self.snapshot, AdbDevicesSnapshot):
            raise TypeError("snapshot must be AdbDevicesSnapshot")

    @property
    def endpoint(self) -> AdbServerEndpoint:
        return self.server.endpoint

    @property
    def epoch(self) -> int:
        return self.server.epoch


AdbTransportSignal: TypeAlias = (
    AdbTransportCommandCompleted
    | AdbTransportEnsureCompleted
    | AdbDevicesTrackingStarted
    | AdbDevicesTrackingStopped
    | AdbDevicesTrackingFailed
    | AdbDevicesSnapshotObserved
)


__all__ = [
    "AdbDevicesSnapshotObserved",
    "AdbDevicesTrackingFailed",
    "AdbDevicesTrackingFailure",
    "AdbDevicesTrackingStarted",
    "AdbDevicesTrackingStopped",
    "AdbTransportCommandCompleted",
    "AdbTransportCommandOperation",
    "AdbTransportEnsureCompleted",
    "AdbTransportSignal",
]
