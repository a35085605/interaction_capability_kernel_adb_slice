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
class AdbServerController(AdbServerProvider, AdbServerStopper, Protocol):
    """Provide and stop ADB server lifetimes."""


__all__ = ["AdbServerController", "AdbServerProvider", "AdbServerStopper"]
