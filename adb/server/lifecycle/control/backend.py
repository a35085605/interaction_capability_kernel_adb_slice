from __future__ import annotations

from typing import Protocol, runtime_checkable

from adb.server.endpoint import AdbServerEndpoint
from adb.server.lifecycle.control.errors import AdbServerAttachmentMismatchError


def require_backend_release_endpoint(
    owned: AdbServerEndpoint,
    requested: AdbServerEndpoint,
) -> None:
    """Reject release of an endpoint other than the exact backend-owned attachment."""

    if not isinstance(owned, AdbServerEndpoint):
        raise TypeError("owned must be AdbServerEndpoint")
    if not isinstance(requested, AdbServerEndpoint):
        raise TypeError("requested must be AdbServerEndpoint")
    if owned != requested:
        raise AdbServerAttachmentMismatchError(
            "requested endpoint does not identify the owned ADB server backend attachment"
        )


@runtime_checkable
class AdbServerBackend(Protocol):
    """Acquire and release one backend-scoped usable ADB server attachment.

    Concrete resource ownership, operation exclusion, and cleanup semantics remain adapter-defined.
    Releasing an attachment relinquishes those backend resources; it does not imply that every
    backend must terminate an underlying ADB server process.
    """

    def acquire(
        self,
        endpoint: AdbServerEndpoint | None = None,
    ) -> AdbServerEndpoint:
        ...

    def release(self, endpoint: AdbServerEndpoint) -> None:
        ...


__all__ = [
    "AdbServerBackend",
    "require_backend_release_endpoint",
]
