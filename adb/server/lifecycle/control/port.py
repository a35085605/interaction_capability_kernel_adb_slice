from __future__ import annotations

from typing import Protocol, runtime_checkable

from adb.server.endpoint import AdbServerEndpoint
from adb.server.identity import AdbServer


@runtime_checkable
class AdbServerController(Protocol):
    """Provide usable ADB server lifetimes and make exact lifetimes unavailable.

    Providing creates one fresh server lifetime. Stopping is keyed by server identity
    so successive lifetimes may reuse an endpoint.
    """

    def provide(
        self,
        endpoint: AdbServerEndpoint | None = None,
    ) -> AdbServer:
        """Provide one fresh usable ADB server lifetime."""
        ...

    def stop(
        self,
        server: AdbServer,
    ) -> None:
        """Return only after the exact server lifetime is proven unavailable."""
        ...


__all__ = ["AdbServerController"]
