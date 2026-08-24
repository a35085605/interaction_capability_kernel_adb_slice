from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import TypeAlias

from adb.server.endpoint import AdbServerEndpoint
from adb.server.identity import AdbServer
from adb.tracking.model import AdbDevicesSnapshot
from adb.tracking.identity import AdbDevicesTrackingScope


def _require_scope(value: object) -> AdbDevicesTrackingScope:
    if not isinstance(value, AdbDevicesTrackingScope):
        raise TypeError("scope must be AdbDevicesTrackingScope")
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


class AdbDevicesTrackingFailure(str, Enum):
    """Reason a track-devices tracker terminated abnormally."""

    SERVER_CONNECTION = "server_connection"
    SERVICE = "service"
    PROTOCOL = "protocol"


class _TrackingScopeSignalProjection:
    scope: AdbDevicesTrackingScope

    @property
    def server(self) -> AdbServer:
        return self.scope.server

    @property
    def server_endpoint(self) -> AdbServerEndpoint:
        return self.scope.server_endpoint

    @property
    def server_epoch(self) -> int:
        return self.scope.server_epoch

    @property
    def generation(self) -> int:
        return self.scope.generation


@dataclass(frozen=True, slots=True)
class AdbDevicesTrackingStarted(_TrackingScopeSignalProjection):
    """Signal that one exact tracker scope entered stream mode."""

    scope: AdbDevicesTrackingScope

    def __post_init__(self) -> None:
        _require_scope(self.scope)


@dataclass(frozen=True, slots=True)
class AdbDevicesTrackingStopped(_TrackingScopeSignalProjection):
    """Signal that one exact tracker scope ended without implying transport disappearance."""

    scope: AdbDevicesTrackingScope

    def __post_init__(self) -> None:
        _require_scope(self.scope)


@dataclass(frozen=True, slots=True)
class AdbDevicesTrackingFailed(_TrackingScopeSignalProjection):
    """Signal that one exact tracker scope failed without synthesizing server state."""

    scope: AdbDevicesTrackingScope
    failure: AdbDevicesTrackingFailure
    diagnostic: str | None = None

    def __post_init__(self) -> None:
        _require_scope(self.scope)
        if not isinstance(self.failure, AdbDevicesTrackingFailure):
            raise TypeError("failure must be AdbDevicesTrackingFailure")
        object.__setattr__(
            self,
            "diagnostic",
            _normalize_optional_text(
                self.diagnostic,
                field_name="ADB track-devices diagnostic",
            ),
        )


@dataclass(frozen=True, slots=True)
class AdbDevicesSnapshotObserved(_TrackingScopeSignalProjection):
    """Signal carrying one complete snapshot emitted by one exact tracker scope."""

    scope: AdbDevicesTrackingScope
    snapshot: AdbDevicesSnapshot

    def __post_init__(self) -> None:
        _require_scope(self.scope)
        if not isinstance(self.snapshot, AdbDevicesSnapshot):
            raise TypeError("snapshot must be AdbDevicesSnapshot")


AdbDevicesTrackingSignal: TypeAlias = (
    AdbDevicesTrackingStarted
    | AdbDevicesTrackingStopped
    | AdbDevicesTrackingFailed
    | AdbDevicesSnapshotObserved
)


__all__ = [
    "AdbDevicesSnapshotObserved",
    "AdbDevicesTrackingFailed",
    "AdbDevicesTrackingFailure",
    "AdbDevicesTrackingSignal",
    "AdbDevicesTrackingStarted",
    "AdbDevicesTrackingStopped",
]
