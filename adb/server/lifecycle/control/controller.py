from __future__ import annotations

from threading import Lock

from adb.epoch import EpochIssuer
from adb.server.endpoint import AdbServerEndpoint
from adb.server.identity import AdbServer, ServerEpoch
from adb.server.lifecycle.control.errors import (
    AdbServerNativeTerminationUnprovenError,
    AdbServerStartDeferredError,
    AdbServerStartError,
    AdbServerStopError,
)
from adb.server.lifecycle.control.backend import AdbServerBackend
from adb.server.provisioning import (
    AdbServerProvisioningState,
    AdbServerProvisioningView,
)


class AdbServerController:
    """Fabricate and retire exact domain server lifetimes over one server backend.

    The controller owns at most one domain :class:`AdbServer` identity at a time.  Accepting
    ``retire(server)`` irreversibly relinquishes that exact domain lifetime before backend release
    is attempted.  Concrete resource ownership remains a backend concern: release may terminate an
    owned server process, detach from a borrowed server, or dispose other adapter-owned resources.
    A release failure never restores controller ownership or revives a retired server epoch.
    """

    def __init__(
        self,
        backend: AdbServerBackend,
        server_epoch_issuer: EpochIssuer[ServerEpoch],
        provisioning: AdbServerProvisioningView | None = None,
    ) -> None:
        if not isinstance(backend, AdbServerBackend):
            raise TypeError("backend must satisfy AdbServerBackend")
        if not isinstance(server_epoch_issuer, EpochIssuer):
            raise TypeError("server_epoch_issuer must satisfy EpochIssuer")
        if provisioning is None:
            provisioning = AdbServerProvisioningState()
        if not isinstance(provisioning, AdbServerProvisioningView):
            raise TypeError("provisioning must satisfy AdbServerProvisioningView or be None")

        self._backend = backend
        self._server_epoch_issuer = server_epoch_issuer
        self._provisioning = provisioning
        self._mutation_lock = Lock()
        self._owned_server: AdbServer | None = None

    def provision(self) -> AdbServer:
        """Synchronously provision one fresh usable domain server lifetime."""

        with self._mutation_lock:
            endpoint = self._provisioning.required_endpoint
            if self._owned_server is not None:
                raise AdbServerStartDeferredError(
                    "the previous ADB server domain lifetime has not yet been relinquished"
                )

            resolved_endpoint = self._backend.acquire(endpoint)
            if not isinstance(resolved_endpoint, AdbServerEndpoint):
                raise TypeError(
                    "server backend acquire() must return AdbServerEndpoint"
                )

            try:
                if endpoint is not None and resolved_endpoint != endpoint:
                    raise AdbServerStartError(
                        "endpoint-constrained ADB server provisioning changed endpoint"
                    )
                server = AdbServer(
                    resolved_endpoint,
                    self._server_epoch_issuer.issue(),
                )
            except BaseException:
                try:
                    self._backend.release(resolved_endpoint)
                except AdbServerNativeTerminationUnprovenError:
                    # Preserve the stronger native fact.  The controller never fabricated a
                    # domain identity, and the backend now requires external intervention.
                    raise
                except BaseException as release_error:
                    raise AdbServerStartError(
                        "ADB server identity creation failed and its backend attachment "
                        "could not be released"
                    ) from release_error
                raise

            self._owned_server = server
            return server

    def retire(self, server: AdbServer) -> None:
        """Retire exact controller ownership, then synchronously release its backend attachment.

        Once exact ownership is accepted it is relinquished under the controller lock and is never
        restored, regardless of the backend release outcome.  Potentially slow adapter-specific
        disposal is deliberately performed outside that lock so a successor ``provision()`` may reach
        the backend while release of the retired attachment is still in progress.
        """

        if not isinstance(server, AdbServer):
            raise TypeError("server must be AdbServer")

        with self._mutation_lock:
            if self._owned_server != server:
                raise AdbServerStopError(
                    "no exact owned ADB server lifetime matches the request"
                )
            self._owned_server = None

        self._backend.release(server.endpoint)


__all__ = ["AdbServerController"]
