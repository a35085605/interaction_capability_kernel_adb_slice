from __future__ import annotations

from typing import Protocol, runtime_checkable

from adb.server.endpoint import AdbServerEndpoint


@runtime_checkable
class AdbServerBackend(Protocol):
    """Own one native ADB server lifetime at a time."""

    def start(
        self,
        endpoint: AdbServerEndpoint | None = None,
    ) -> AdbServerEndpoint:
        ...

    def stop(self, endpoint: AdbServerEndpoint) -> None:
        ...


__all__ = ["AdbServerBackend"]
