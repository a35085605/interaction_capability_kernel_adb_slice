from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from adb.server.endpoint import AdbServerEndpoint
from adb.server.identity import AdbServer


class AdbServerControlError(RuntimeError):
    """Base error for ADB server controller failures."""


class AdbServerStartError(AdbServerControlError):
    """A controller could not establish one fresh ADB server lifetime."""


class AdbServerStopError(AdbServerControlError):
    """A controller could not prove termination of the requested ADB server lifetime."""


@dataclass(frozen=True, slots=True)
class AdbServerStart:
    """Successful establishment of one fresh ADB server identity."""

    server: AdbServer

    def __post_init__(self) -> None:
        if not isinstance(self.server, AdbServer):
            raise TypeError("server must be AdbServer")

    @property
    def endpoint(self) -> AdbServerEndpoint:
        return self.server.endpoint


@dataclass(frozen=True, slots=True)
class AdbServerStop:
    """Evidence that one ADB server was stopped."""

    server: AdbServer

    def __post_init__(self) -> None:
        if not isinstance(self.server, AdbServer):
            raise TypeError("server must be AdbServer")

    @property
    def endpoint(self) -> AdbServerEndpoint:
        return self.server.endpoint


@runtime_checkable
class AdbServerController(Protocol):
    """Start and stop ADB server lifetimes.

    Stopping is keyed by server identity so successive lifetimes may reuse an endpoint.
    """

    def start(
        self,
        endpoint: AdbServerEndpoint | None = None,
    ) -> AdbServerStart: ...

    def stop(
        self,
        server: AdbServer,
    ) -> AdbServerStop: ...


__all__ = [
    "AdbServerControlError",
    "AdbServerController",
    "AdbServerStart",
    "AdbServerStartError",
    "AdbServerStop",
    "AdbServerStopError",
]
