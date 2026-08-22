from __future__ import annotations

from typing import Protocol, runtime_checkable

from adb.server.model import AdbServerEndpoint


class AdbServerNativeError(RuntimeError):
    """Base error for exact native ADB server lifecycle failures."""


class AdbServerCloseError(AdbServerNativeError):
    """An owned native ADB server process could not be proven terminated."""


@runtime_checkable
class AdbServerNativeHandle(Protocol):
    """Exact native lifetime handle returned by one successful launch.

    The handle is the private authority for the exact native lifetime and its teardown. Public
    incarnation identity and ownership relationship are assigned separately. Implementations must never
    represent a pre-existing ADB listener that was merely discovered by endpoint.
    """

    @property
    def endpoint(self) -> AdbServerEndpoint: ...

    @property
    def active(self) -> bool: ...

    def close(self) -> None:
        """Terminate this exact native lifetime, or raise when termination is not proven."""
        ...


__all__ = [
    "AdbServerCloseError",
    "AdbServerNativeError",
    "AdbServerNativeHandle",
]
