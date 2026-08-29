from __future__ import annotations

from adb.server.address import AdbServerAddress
from adb.server.identity import AdbServer
from adb.server.lifecycle.control.backend import (
    AdbServerBackend,
    AdbServerBackendFailed,
    AdbServerBackendOperationBlocked,
    AdbServerBackendOperationInProgress,
    AdbServerBackendSatisfied,
    AdbServerBackendSucceeded,
)


def _release_backend_attachment(
    backend: AdbServerBackend,
    endpoint: AdbServerAddress,
) -> None:
    result = backend.release(endpoint)
    if isinstance(
        result,
        (
            AdbServerBackendSucceeded,
            AdbServerBackendSatisfied,
            AdbServerBackendOperationInProgress,
            AdbServerBackendOperationBlocked,
            AdbServerBackendFailed,
        ),
    ):
        return
    raise TypeError("server backend release() returned an unsupported result")


class AdbServerRetirer:
    """Release backend attachments belonging to retired ADB server lifetimes."""

    def __init__(self, backend: AdbServerBackend) -> None:
        if not isinstance(backend, AdbServerBackend):
            raise TypeError("backend must satisfy AdbServerBackend")
        self._backend = backend

    def retire(self, server: AdbServer) -> None:
        """Request release of the backend attachment for one domain server lifetime."""

        if not isinstance(server, AdbServer):
            raise TypeError("server must be AdbServer")
        _release_backend_attachment(self._backend, server.endpoint)


__all__ = ["AdbServerRetirer"]
