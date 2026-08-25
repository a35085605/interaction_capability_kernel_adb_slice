from __future__ import annotations

from typing import Protocol, runtime_checkable

from adb.server.endpoint import AdbServerEndpoint


@runtime_checkable
class AdbServerBackend(Protocol):
    """Own native ADB server lifecycle state independently of domain retirement.

    ``stop()`` may remain in progress after the controller has relinquished its domain server.
    A concurrent ``start()`` must decide from backend-native state whether provisioning is safe;
    callers must not serialize successor provisioning on native teardown completion.
    """

    def start(
        self,
        endpoint: AdbServerEndpoint | None = None,
    ) -> AdbServerEndpoint:
        ...

    def stop(self, endpoint: AdbServerEndpoint) -> None:
        ...


__all__ = ["AdbServerBackend"]
