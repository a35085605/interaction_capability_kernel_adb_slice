from __future__ import annotations

from typing import Protocol, runtime_checkable

from adb.server.endpoint import AdbServerEndpoint


@runtime_checkable
class AdbEndpointStarter(Protocol):
    """Synchronously establish one owned native ADB server endpoint lifetime."""

    def start(
        self,
        endpoint: AdbServerEndpoint | None = None,
    ) -> AdbServerEndpoint:
        """Return only after one owned endpoint is usable."""
        ...


@runtime_checkable
class AdbEndpointStopper(Protocol):
    """Synchronously stop one owned native ADB server endpoint lifetime."""

    def stop(self, endpoint: AdbServerEndpoint) -> None:
        """Return only after the owned native lifetime at endpoint is proven stopped."""
        ...


@runtime_checkable
class AdbEndpointController(AdbEndpointStarter, AdbEndpointStopper, Protocol):
    """Synchronously own one native ADB server endpoint lifetime at a time."""


__all__ = [
    "AdbEndpointController",
    "AdbEndpointStarter",
    "AdbEndpointStopper",
]
