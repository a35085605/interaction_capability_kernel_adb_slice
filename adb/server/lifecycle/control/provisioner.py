from __future__ import annotations

from networking import TcpAddress
from adb.server.endpoint import AdbServerEndpoint
from adb.server.lifecycle.control.backend import (
    AdbServerBackend,
    AdbServerBackendFailed,
    AdbServerBackendOperationBlocked,
    AdbServerBackendOperationInProgress,
    AdbServerBackendSatisfied,
    AdbServerBackendSucceeded,
)
from adb.server.lifecycle.control.result import (
    AdbServerProvisionDeferred,
    AdbServerProvisionFailed,
    AdbServerProvisionResult,
    AdbServerProvisioned,
)
from adb.server.lifecycle.control.retirer import _release_backend_attachment


class AdbServerProvisioner:
    """Provision usable ADB server backend endpoints under one fixed configuration."""

    def __init__(
        self,
        backend: AdbServerBackend,
        *,
        endpoint: AdbServerEndpoint | None,
    ) -> None:
        if not isinstance(backend, AdbServerBackend):
            raise TypeError("backend must satisfy AdbServerBackend")
        if endpoint is not None and not isinstance(endpoint, TcpAddress):
            raise TypeError("endpoint must be TcpAddress or None")
        self._backend = backend
        self._endpoint = endpoint

    @property
    def endpoint(self) -> AdbServerEndpoint | None:
        """Endpoint constraint bound to every provisioning attempt."""

        return self._endpoint

    @staticmethod
    def _backend_busy_diagnostic(
        result: AdbServerBackendOperationInProgress | AdbServerBackendOperationBlocked,
        *,
        requested_operation: str,
    ) -> str:
        if isinstance(result, AdbServerBackendOperationInProgress):
            return result.diagnostic or (
                f"ADB server backend {requested_operation} is already in progress"
            )
        return result.diagnostic

    def _acquire_backend(
        self,
    ) -> AdbServerEndpoint | AdbServerProvisionDeferred | AdbServerProvisionFailed:
        result = self._backend.acquire(self._endpoint)
        if isinstance(result, (AdbServerBackendSucceeded, AdbServerBackendSatisfied)):
            return result.endpoint
        if isinstance(
            result,
            (AdbServerBackendOperationInProgress, AdbServerBackendOperationBlocked),
        ):
            return AdbServerProvisionDeferred(
                self._backend_busy_diagnostic(result, requested_operation="acquire")
            )
        if isinstance(result, AdbServerBackendFailed):
            return AdbServerProvisionFailed(result.diagnostic)
        raise TypeError("server backend acquire() returned an unsupported result")

    def provision(self) -> AdbServerProvisionResult:
        """Synchronously attempt to provision one usable backend endpoint."""

        acquire_result = self._acquire_backend()
        if isinstance(acquire_result, (AdbServerProvisionDeferred, AdbServerProvisionFailed)):
            return acquire_result
        resolved_endpoint = acquire_result

        if self._endpoint is not None and resolved_endpoint != self._endpoint:
            _release_backend_attachment(self._backend, resolved_endpoint)
            return AdbServerProvisionFailed(
                "endpoint-constrained ADB server provisioning changed endpoint"
            )

        return AdbServerProvisioned(resolved_endpoint)


__all__ = ["AdbServerProvisioner"]
