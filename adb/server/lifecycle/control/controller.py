from __future__ import annotations

from threading import Lock

from adb.epoch import EpochIssuer
from adb.server.endpoint import AdbServerEndpoint
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


class AdbServerController:
    """Own one domain ADB server lifetime over a server backend.

    Provisioning translates backend outcomes into domain results. Retiring a server relinquishes
    domain ownership before backend release so successor provisioning can proceed independently.
    """

    def __init__(
        self,
        backend: AdbServerBackend,
        server_epoch_issuer: EpochIssuer[ServerEpoch],
    ) -> None:
        if not isinstance(backend, AdbServerBackend):
            raise TypeError("backend must satisfy AdbServerBackend")
        if not isinstance(server_epoch_issuer, EpochIssuer):
            raise TypeError("server_epoch_issuer must satisfy EpochIssuer")
        self._backend = backend
        self._server_epoch_issuer = server_epoch_issuer
        self._mutation_lock = Lock()
        self._owned_server: AdbServer | None = None

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
        endpoint: AdbServerEndpoint | None,
    ) -> AdbServerEndpoint | AdbServerProvisionDeferred | AdbServerProvisionFailed:
        result = self._backend.acquire(endpoint)
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

    def _release_backend(self, endpoint: AdbServerEndpoint) -> None:
        result = self._backend.release(endpoint)
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

    def provision(self, endpoint: AdbServerEndpoint | None) -> AdbServerProvisionResult:
        """Synchronously attempt to provision one fresh usable domain server lifetime."""

        if endpoint is not None and not isinstance(endpoint, AdbServerEndpoint):
            raise TypeError("endpoint must be AdbServerEndpoint or None")

        with self._mutation_lock:
            if self._owned_server is not None:
                return AdbServerProvisionDeferred(
                    "the previous ADB server domain lifetime has not yet been relinquished"
                )

            acquire_result = self._acquire_backend(endpoint)
            if isinstance(acquire_result, (AdbServerProvisionDeferred, AdbServerProvisionFailed)):
                return acquire_result
            resolved_endpoint = acquire_result

            if endpoint is not None and resolved_endpoint != endpoint:
                self._release_backend(resolved_endpoint)
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
                    self._release_backend(resolved_endpoint)
                except BaseException as release_error:
                    raise AdbServerControlError(
                        "ADB server identity creation failed and its backend attachment "
                        "could not be released"
                    ) from release_error
                raise

            self._owned_server = server
            return AdbServerProvisioned(server)

    def retire(self, server: AdbServer) -> None:
        """Relinquish exact domain ownership before requesting backend release."""

        if not isinstance(server, AdbServer):
            raise TypeError("server must be AdbServer")

        with self._mutation_lock:
            if self._owned_server != server:
                raise AdbServerControlError(
                    "no exact owned ADB server lifetime matches the request"
                )
            self._owned_server = None

        self._release_backend(server.endpoint)


__all__ = ["AdbServerController"]
