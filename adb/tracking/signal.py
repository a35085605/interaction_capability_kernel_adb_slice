from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import TypeAlias

from networking import TcpAddress
from adb.server.epoch import ServerEpoch
from adb.server.lifetime import AdbServerLifetime
from adb.tracking.snapshot.identity import AdbTransportListSnapshot


def _require_server(value: object) -> AdbServerLifetime:
    if not isinstance(value, AdbServerLifetime):
        raise TypeError("server must be AdbServerLifetime")
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


class AdbTransportListWatchFailure(str, Enum):
    """Reason a transport-list watch terminated abnormally."""

    SERVER_CONNECTION = "server_connection"
    SERVICE = "service"
    PROTOCOL = "protocol"


class _TransportListWatchServerSignalProjection:
    server: AdbServerLifetime

    @property
    def server_endpoint(self) -> TcpAddress:
        return self.server.endpoint

    @property
    def server_epoch(self) -> ServerEpoch:
        return self.server.epoch


@dataclass(frozen=True, slots=True)
class AdbTransportListWatchStarted(_TransportListWatchServerSignalProjection):
    """Signal that a transport-list watch entered stream mode for one server lifetime."""

    server: AdbServerLifetime

    def __post_init__(self) -> None:
        _require_server(self.server)


@dataclass(frozen=True, slots=True)
class AdbTransportListWatchStopped(_TransportListWatchServerSignalProjection):
    """Signal that a transport-list watch ended while preserving the last observed transport
    evidence.
    """

    server: AdbServerLifetime

    def __post_init__(self) -> None:
        _require_server(self.server)


@dataclass(frozen=True, slots=True)
class AdbTransportListWatchFailed(_TransportListWatchServerSignalProjection):
    """Signal a transport-list watch failure while preserving authoritative server state."""

    server: AdbServerLifetime
    failure: AdbTransportListWatchFailure
    diagnostic: str | None = None

    def __post_init__(self) -> None:
        _require_server(self.server)
        if not isinstance(self.failure, AdbTransportListWatchFailure):
            raise TypeError("failure must be AdbTransportListWatchFailure")
        object.__setattr__(
            self,
            "diagnostic",
            _normalize_optional_text(
                self.diagnostic,
                field_name="ADB transport-list watch diagnostic",
            ),
        )


@dataclass(frozen=True, slots=True)
class AdbTransportListSnapshotObserved(_TransportListWatchServerSignalProjection):
    """Signal carrying one complete snapshot observed from one server lifetime."""

    server: AdbServerLifetime
    snapshot: AdbTransportListSnapshot

    def __post_init__(self) -> None:
        _require_server(self.server)
        if not isinstance(self.snapshot, AdbTransportListSnapshot):
            raise TypeError("snapshot must be AdbTransportListSnapshot")


AdbTransportListWatchSignal: TypeAlias = (
    AdbTransportListWatchStarted
    | AdbTransportListWatchStopped
    | AdbTransportListWatchFailed
    | AdbTransportListSnapshotObserved
)


__all__ = [
    "AdbTransportListSnapshotObserved",
    "AdbTransportListWatchFailed",
    "AdbTransportListWatchFailure",
    "AdbTransportListWatchSignal",
    "AdbTransportListWatchStarted",
    "AdbTransportListWatchStopped",
]
