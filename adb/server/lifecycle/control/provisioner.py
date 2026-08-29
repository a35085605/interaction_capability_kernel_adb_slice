from __future__ import annotations

from adb.epoch import EpochIssuer
from adb.server.address import AdbServerAddress
from adb.server.identity import AdbServer, ServerEpoch
from adb.server.lifecycle.control.backend import (
    AdbServerBackend,
    AdbServerBackendFailed,
    AdbServerBackendOperationBlocked,
    AdbServerBackendOperationInProgress,
    AdbServerBackendSatisfied,
    AdbServerBackendSucceeded,
)
from adb.server.lifecycle.control.errors import AdbServerControlError
from adb.server.lifecycle.control.result import (
    AdbServerProvisionDeferred,
    AdbServerProvisionFailed,
    AdbServerProvisionResult,
    AdbServerProvisioned,
)
from adb.server.lifecycle.control.retirer import _release_backend_attachment


class AdbServerProvisioner:
    """Provision fresh ADB server lifetimes under one fixed endpoint configuration."""

    def __init__(
        self,
        backend: AdbServerBackend,
        server_epoch_issuer: EpochIssuer[ServerEpoch],
        *,
        endpoint: AdbServerAddress | None,
    ) -> None:
        if not isinstance(backend, AdbServerBackend):
            raise TypeError("backend must satisfy AdbServerBackend")
        if not isinstance(server_epoch_issuer, EpochIssuer):
            raise TypeError("server_epoch_issuer must satisfy EpochIssuer")
        if endpoint is not None and not isinstance(endpoint, AdbServerAddress):
            raise TypeError("endpoint must be AdbServerAddress or None")
        self._backend = backend
        self._server_epoch_issuer = server_epoch_issuer
        self._endpoint = endpoint

    @property
    def endpoint(self) -> AdbServerAddress | None:
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
    ) -> AdbServerAddress | AdbServerProvisionDeferred | AdbServerProvisionFailed:
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
        """Synchronously attempt to provision one fresh usable domain server lifetime."""

        acquire_result = self._acquire_backend()
        if isinstance(acquire_result, (AdbServerProvisionDeferred, AdbServerProvisionFailed)):
            return acquire_result
        resolved_endpoint = acquire_result

        if self._endpoint is not None and resolved_endpoint != self._endpoint:
            _release_backend_attachment(self._backend, resolved_endpoint)
            return AdbServerProvisionFailed(
                "endpoint-constrained ADB server provisioning changed endpoint"
            )

        try:
            server = AdbServer(
                resolved_endpoint,
                self._server_epoch_issuer.issue(),
            )
        except BaseException:
            try:
                _release_backend_attachment(self._backend, resolved_endpoint)
            except BaseException as release_error:
                raise AdbServerControlError(
                    "ADB server identity creation failed and its backend attachment "
                    "could not be released"
                ) from release_error
            raise

        return AdbServerProvisioned(server)


__all__ = ["AdbServerProvisioner"]
