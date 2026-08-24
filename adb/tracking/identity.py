from __future__ import annotations

from dataclasses import dataclass
from threading import Lock
from typing import Protocol, runtime_checkable

from adb.server.endpoint import AdbServerEndpoint
from adb.server.identity import AdbServer


@dataclass(frozen=True, slots=True)
class AdbDevicesTrackingScope:
    """One track-devices observation session.

    ``generation`` distinguishes successive tracker sessions for correlation and stale-signal
    fencing. It is not a generation of the observed device data: replacement trackers bound to
    the same ``AdbServer`` lifetime observe the same server-epoch data world.
    """

    server: AdbServer
    generation: int

    def __post_init__(self) -> None:
        if not isinstance(self.server, AdbServer):
            raise TypeError("server must be AdbServer")
        if isinstance(self.generation, bool) or not isinstance(self.generation, int):
            raise TypeError("generation must be an integer")
        if self.generation <= 0:
            raise ValueError("generation must be greater than zero")

    @property
    def server_endpoint(self) -> AdbServerEndpoint:
        return self.server.endpoint

    @property
    def server_epoch(self) -> int:
        return self.server.epoch


@runtime_checkable
class AdbDevicesTrackingGenerationIssuer(Protocol):
    """Issue correlation generations for successive track-devices sessions."""

    def issue(self) -> int:
        ...


class AdbDevicesTrackingGenerationSequence:
    """Runtime-scoped monotonically increasing tracking-session generation issuer."""

    def __init__(self) -> None:
        self._lock = Lock()
        self._current = 0

    def issue(self) -> int:
        with self._lock:
            self._current += 1
            return self._current


__all__ = [
    "AdbDevicesTrackingScope",
    "AdbDevicesTrackingGenerationIssuer",
    "AdbDevicesTrackingGenerationSequence",
]
