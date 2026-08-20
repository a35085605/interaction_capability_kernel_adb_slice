from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import TypeAlias

from adb.server.endpoint import AdbServerEndpoint
from adb.transport.inventory.model import (
    AdbDevicesSnapshot,
    AdbDevicesTrackingSessionId,
)
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


def _require_endpoint(value: object) -> AdbServerEndpoint:
    if not isinstance(value, AdbServerEndpoint):
        raise TypeError("endpoint must be AdbServerEndpoint")
    return value


def _require_tracking_session_id(value: object) -> AdbDevicesTrackingSessionId:
    if not isinstance(value, AdbDevicesTrackingSessionId):
        raise TypeError("session_id must be AdbDevicesTrackingSessionId")
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
    """Signal carrying the result of one atomic ADB transport command attempt."""

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
    """Typed reason one transport-inventory tracking session terminated abnormally."""

    SERVER_CONNECTION = "server_connection"
    SERVICE = "service"
    PROTOCOL = "protocol"


@dataclass(frozen=True, slots=True)
class AdbDevicesTrackingStarted:
    """Signal that one transport-inventory tracking session entered stream mode."""

    endpoint: AdbServerEndpoint
    session_id: AdbDevicesTrackingSessionId

    def __post_init__(self) -> None:
        _require_endpoint(self.endpoint)
        _require_tracking_session_id(self.session_id)
        if self.session_id.endpoint != self.endpoint:
            raise ValueError("session_id endpoint must match signal endpoint")


@dataclass(frozen=True, slots=True)
class AdbDevicesTrackingStopped:
    """Signal that tracking ended without implying transport disappearance."""

    endpoint: AdbServerEndpoint
    session_id: AdbDevicesTrackingSessionId

    def __post_init__(self) -> None:
        _require_endpoint(self.endpoint)
        _require_tracking_session_id(self.session_id)
        if self.session_id.endpoint != self.endpoint:
            raise ValueError("session_id endpoint must match signal endpoint")


@dataclass(frozen=True, slots=True)
class AdbDevicesTrackingFailed:
    """Signal that tracking failed without synthesizing server or transport state."""

    endpoint: AdbServerEndpoint
    session_id: AdbDevicesTrackingSessionId
    failure: AdbDevicesTrackingFailure
    diagnostic: str | None = None

    def __post_init__(self) -> None:
        _require_endpoint(self.endpoint)
        _require_tracking_session_id(self.session_id)
        if self.session_id.endpoint != self.endpoint:
            raise ValueError("session_id endpoint must match signal endpoint")
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


@dataclass(frozen=True, slots=True)
class AdbDevicesSnapshotObserved:
    """Signal carrying one complete snapshot emitted by ADB track-devices."""

    endpoint: AdbServerEndpoint
    session_id: AdbDevicesTrackingSessionId
    snapshot: AdbDevicesSnapshot

    def __post_init__(self) -> None:
        _require_endpoint(self.endpoint)
        _require_tracking_session_id(self.session_id)
        if self.session_id.endpoint != self.endpoint:
            raise ValueError("session_id endpoint must match signal endpoint")
        if not isinstance(self.snapshot, AdbDevicesSnapshot):
            raise TypeError("snapshot must be AdbDevicesSnapshot")


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
