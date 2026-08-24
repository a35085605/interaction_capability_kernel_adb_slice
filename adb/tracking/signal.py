from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import TypeAlias

from adb.server.endpoint import AdbServerEndpoint
from adb.server.identity import AdbServer, ServerEpoch
from adb.tracking.snapshot.identity import AdbDevicesSnapshot


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


class AdbDevicesTrackingFailure(str, Enum):
    """Reason a track-devices tracker terminated abnormally."""

    SERVER_CONNECTION = "server_connection"
    SERVICE = "service"
    PROTOCOL = "protocol"


class _TrackingServerSignalProjection:
    server: AdbServer

    @property
    def server_endpoint(self) -> AdbServerEndpoint:
        return self.server.endpoint

    @property
    def server_epoch(self) -> ServerEpoch:
        return self.server.epoch


@dataclass(frozen=True, slots=True)
class AdbDevicesTrackingStarted(_TrackingServerSignalProjection):
    """Signal that device tracking entered stream mode for one server lifetime."""

    server: AdbServer

    def __post_init__(self) -> None:
        _require_server(self.server)


@dataclass(frozen=True, slots=True)
class AdbDevicesTrackingStopped(_TrackingServerSignalProjection):
    """Signal that device tracking ended without implying transport disappearance."""

    server: AdbServer

    def __post_init__(self) -> None:
        _require_server(self.server)


@dataclass(frozen=True, slots=True)
class AdbDevicesTrackingFailed(_TrackingServerSignalProjection):
    """Signal that device tracking failed without synthesizing server state."""

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
                field_name="ADB track-devices diagnostic",
            ),
        )


@dataclass(frozen=True, slots=True)
class AdbDevicesSnapshotObserved(_TrackingServerSignalProjection):
    """Signal carrying one complete snapshot observed from one server lifetime."""

    server: AdbServer
    snapshot: AdbDevicesSnapshot

    def __post_init__(self) -> None:
        _require_server(self.server)
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
