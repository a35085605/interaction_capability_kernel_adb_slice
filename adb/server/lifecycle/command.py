from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from adb.server.endpoint import AdbServerEndpoint
from native_attempt import NativeAttemptResult


@dataclass(frozen=True, slots=True)
class AdbServerStart:
    """Request one native attempt to start the ADB server at one endpoint."""

    endpoint: AdbServerEndpoint

    def __post_init__(self) -> None:
        if not isinstance(self.endpoint, AdbServerEndpoint):
            raise TypeError("endpoint must be AdbServerEndpoint")


@dataclass(frozen=True, slots=True)
class AdbServerStop:
    """Request one native attempt to stop the ADB server at one endpoint."""

    endpoint: AdbServerEndpoint

    def __post_init__(self) -> None:
        if not isinstance(self.endpoint, AdbServerEndpoint):
            raise TypeError("endpoint must be AdbServerEndpoint")


class AdbServerStarter(Protocol):
    def start(self, operation: AdbServerStart) -> NativeAttemptResult: ...


class AdbServerStopper(Protocol):
    def stop(self, operation: AdbServerStop) -> NativeAttemptResult: ...


__all__ = ["AdbServerStart", "AdbServerStarter", "AdbServerStop", "AdbServerStopper"]
