from __future__ import annotations

from dataclasses import dataclass
from threading import Lock
from typing import Protocol, runtime_checkable

from adb.server.endpoint import AdbServerEndpoint
from adb.server.identity import AdbServer


@dataclass(frozen=True, slots=True)
class AdbDevicesTrackingScopeIdentity:
    """Identity for one track-devices lifetime.

    The generation distinguishes successive tracker scopes bound to the same ADB server
    lifetime. A scope identity is therefore stable for all signals emitted by one tracker and
    changes whenever supervision creates a replacement tracker.
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
    def endpoint(self) -> AdbServerEndpoint:
        return self.server.endpoint

    @property
    def epoch(self) -> int:
        return self.server.epoch


@runtime_checkable
class AdbDevicesTrackingGenerationIssuer(Protocol):
    """Issue generations for successive track-devices tracker scopes."""

    def issue(self) -> int:
        ...


class AdbDevicesTrackingGenerationSequence:
    """Runtime-scoped monotonically increasing tracking-scope generation issuer."""

    def __init__(self) -> None:
        self._lock = Lock()
        self._current = 0

    def issue(self) -> int:
        with self._lock:
            self._current += 1
            return self._current


__all__ = [
    "AdbDevicesTrackingGenerationIssuer",
    "AdbDevicesTrackingGenerationSequence",
    "AdbDevicesTrackingScopeIdentity",
]
