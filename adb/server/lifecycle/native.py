from __future__ import annotations

from typing import Protocol, runtime_checkable

from adb.server.model import AdbServerEndpoint


class AdbServerNativeError(RuntimeError):
    """Base error for process-owned native ADB server lifecycle failures."""


class AdbServerLaunchError(AdbServerNativeError):
    """A fresh process-owned native ADB server could not be launched."""


class AdbServerCloseError(AdbServerNativeError):
    """An owned native ADB server process could not be proven terminated."""


@runtime_checkable
class AdbServerNativeHandle(Protocol):
    """Exact native lifetime handle returned by one successful launch.

    The handle is the authority for both ownership identity and teardown. Implementations
    must never represent a pre-existing ADB listener that was merely discovered by endpoint.
    """

    @property
    def endpoint(self) -> AdbServerEndpoint: ...

    @property
    def active(self) -> bool: ...

    def close(self) -> None:
        """Terminate this exact native lifetime, or raise when termination is not proven."""
        ...


@runtime_checkable
class AdbServerLauncher(Protocol):
    """Atomically create one fresh native ADB server and return its ownership handle.

    A successful return transfers exact close authority to the returned handle. A launch
    failure must not be represented as an owned server and must not adopt an existing listener.
    """

    def launch(self) -> AdbServerNativeHandle: ...


__all__ = [
    "AdbServerCloseError",
    "AdbServerLaunchError",
    "AdbServerLauncher",
    "AdbServerNativeError",
    "AdbServerNativeHandle",
]
