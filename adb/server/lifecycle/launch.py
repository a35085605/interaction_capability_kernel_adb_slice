from __future__ import annotations

from typing import Protocol, runtime_checkable

from adb.server.lifecycle.handle import AdbServerNativeError, AdbServerNativeHandle


class AdbServerLaunchError(AdbServerNativeError):
    """A fresh process-owned native ADB server could not be launched."""


@runtime_checkable
class AdbServerLauncher(Protocol):
    """Atomically create one fresh native ADB server and return its ownership handle.

    A successful return transfers exact close authority to the returned handle. A launch
    failure must not be represented as an owned server and must not adopt an existing listener.
    """

    def launch(self) -> AdbServerNativeHandle: ...


__all__ = ["AdbServerLaunchError", "AdbServerLauncher"]
