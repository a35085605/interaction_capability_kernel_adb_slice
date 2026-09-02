from __future__ import annotations

from networking import TcpAddress
from adb.server.endpoint import AdbServerEndpoint
from adb.server.lifecycle.control.backend import (
    AdbServerBackend,
    AdbServerBackendFailed,
    AdbServerBackendOperationBlocked,
    AdbServerBackendOperationInProgress,
    AdbServerBackendResult,
    AdbServerBackendSatisfied,
    AdbServerBackendSucceeded,
)


def _release_backend_attachment(
    backend: AdbServerBackend,
    endpoint: AdbServerEndpoint,
) -> AdbServerBackendResult:
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
        return result
    raise TypeError("server backend release() returned an unsupported result")


class AdbServerRetirer:
    """Release backend attachments by ADB server endpoint."""

    def __init__(self, backend: AdbServerBackend) -> None:
        if not isinstance(backend, AdbServerBackend):
            raise TypeError("backend must satisfy AdbServerBackend")
        self._backend = backend

    def retire(self, endpoint: AdbServerEndpoint) -> AdbServerBackendResult:
        """Request release and return the backend's typed operational result."""

        if not isinstance(endpoint, TcpAddress):
            raise TypeError("endpoint must be TcpAddress")
        return _release_backend_attachment(self._backend, endpoint)


__all__ = ["AdbServerRetirer"]
