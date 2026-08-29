from __future__ import annotations

from adb.server.address import AdbServerTcpAddress
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
    endpoint: AdbServerTcpAddress,
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
    """Release backend attachments by ADB server endpoint."""

    def __init__(self, backend: AdbServerBackend) -> None:
        if not isinstance(backend, AdbServerBackend):
            raise TypeError("backend must satisfy AdbServerBackend")
        self._backend = backend

    def retire(self, endpoint: AdbServerTcpAddress) -> None:
        """Request release of the backend attachment identified by ``endpoint``."""

        if not isinstance(endpoint, AdbServerTcpAddress):
            raise TypeError("endpoint must be AdbServerTcpAddress")
        _release_backend_attachment(self._backend, endpoint)


__all__ = ["AdbServerRetirer"]
