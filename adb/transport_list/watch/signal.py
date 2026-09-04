from __future__ import annotations

from dataclasses import dataclass
from typing import TypeAlias

from adb.server.identity import AdbServerIdentity
from adb.transport_list.watch.failure import AdbTransportListWatchFailure


def _require_server(value: object) -> AdbServerIdentity:
    if not isinstance(value, AdbServerIdentity):
        raise TypeError("server must be AdbServerIdentity")
    return value


class _TransportListWatchServerSignalProjection:
    server: AdbServerIdentity

    @property
    def server_identity(self) -> AdbServerIdentity:
        return self.server


@dataclass(frozen=True, slots=True)
class AdbTransportListWatchStarted(_TransportListWatchServerSignalProjection):
    """Signal that a transport-list watch entered stream mode for one server lifetime."""

    server: AdbServerIdentity

    def __post_init__(self) -> None:
        _require_server(self.server)


@dataclass(frozen=True, slots=True)
class AdbTransportListWatchStopped(_TransportListWatchServerSignalProjection):
    """Signal that a transport-list watch ended while preserving the last observed transport
    evidence.
    """

    server: AdbServerIdentity

    def __post_init__(self) -> None:
        _require_server(self.server)


@dataclass(frozen=True, slots=True)
class AdbTransportListWatchFailed(_TransportListWatchServerSignalProjection):
    """Signal a transport-list watch failure while preserving authoritative server state."""

    server: AdbServerIdentity
    failure: AdbTransportListWatchFailure

    def __post_init__(self) -> None:
        _require_server(self.server)
        if not isinstance(self.failure, AdbTransportListWatchFailure):
            raise TypeError("failure must be AdbTransportListWatchFailure")


AdbTransportListWatchSignal: TypeAlias = (
    AdbTransportListWatchStarted
    | AdbTransportListWatchStopped
    | AdbTransportListWatchFailed
)


__all__ = [
    "AdbTransportListWatchFailed",
    "AdbTransportListWatchFailure",
    "AdbTransportListWatchSignal",
    "AdbTransportListWatchStarted",
    "AdbTransportListWatchStopped",
]
