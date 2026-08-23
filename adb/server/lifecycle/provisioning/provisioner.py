from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from adb.server.endpoint import AdbServerEndpoint
from adb.server.identity import AdbServer
from adb.server.lifecycle.control.port import AdbServerProvider


@runtime_checkable
class AdbServerProvisioner(Protocol):
    """Provision one replacement ADB server without exposing endpoint selection."""

    def provision(self) -> AdbServer:
        """Provision one fresh usable ADB server lifetime."""
        ...


@dataclass(frozen=True, slots=True)
class AdbServerControllerProvisioner:
    """Bind a server provider to one already-resolved endpoint constraint."""

    provider: AdbServerProvider
    endpoint: AdbServerEndpoint | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.provider, AdbServerProvider):
            raise TypeError("provider must satisfy AdbServerProvider")
        if self.endpoint is not None and not isinstance(
            self.endpoint, AdbServerEndpoint
        ):
            raise TypeError("endpoint must be AdbServerEndpoint or None")

    def provision(self) -> AdbServer:
        server = self.provider.provide(self.endpoint)
        if not isinstance(server, AdbServer):
            raise TypeError("provider.provide() must return AdbServer")
        if self.endpoint is not None and server.endpoint != self.endpoint:
            raise ValueError("endpoint-constrained server provisioning changed endpoint")
        return server


__all__ = ["AdbServerControllerProvisioner", "AdbServerProvisioner"]
