from __future__ import annotations

from typing import Protocol, runtime_checkable

from adb.server.endpoint import AdbServerEndpoint
from adb.server.identity import AdbServer


@runtime_checkable
class AdbServerProvider(Protocol):
    """Provide fresh usable ADB server lifetimes."""

    def provide(
        self,
        endpoint: AdbServerEndpoint | None = None,
    ) -> AdbServer:
        """Provide one fresh usable ADB server lifetime."""
        ...


@runtime_checkable
class AdbServerStopper(Protocol):
    """Make exact ADB server lifetimes unavailable."""

    def stop(
        self,
        server: AdbServer,
    ) -> None:
        """Return only after the exact server lifetime is proven unavailable."""
        ...


@runtime_checkable
class AdbEndpointController(Protocol):
    """Synchronously own one native ADB server endpoint lifetime at a time."""

    def start(
        self,
        endpoint: AdbServerEndpoint | None = None,
    ) -> AdbServerEndpoint:
        """Return only after one owned endpoint is usable."""
        ...

    def stop(self, endpoint: AdbServerEndpoint) -> None:
        """Return only after the owned native lifetime at endpoint is proven stopped."""
        ...


__all__ = ["AdbEndpointController", "AdbServerProvider", "AdbServerStopper"]
