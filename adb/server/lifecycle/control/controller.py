from __future__ import annotations

from threading import Lock

from adb.epoch import EpochIssuer
from adb.server.endpoint import AdbServerEndpoint
from adb.server.identity import AdbServer, ServerEpoch
from adb.server.lifecycle.control.errors import AdbServerStartError, AdbServerStopError
from adb.server.lifecycle.control.port import AdbServerBackend


class AdbServerController:
    """Fabricate and dispose exact domain server lifetimes over one server backend.

    The server backend owns native resource identity.  This facade owns only the current
    :class:`AdbServer` identity and serializes domain-facing mutations so an endpoint can never be
    rebound to a newer server lifetime while an older one is still active or unproven stopped.
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
        self._active_server: AdbServer | None = None

    def provide(
        self,
        endpoint: AdbServerEndpoint | None = None,
    ) -> AdbServer:
        """Synchronously provide one fresh usable domain server lifetime."""

        if endpoint is not None and not isinstance(endpoint, AdbServerEndpoint):
            raise TypeError("endpoint must be AdbServerEndpoint or None")

        with self._mutation_lock:
            if self._active_server is not None:
                raise AdbServerStartError(
                    "an ADB server lifetime is already active in this controller"
                )

            resolved_endpoint = self._backend.start(endpoint)
            if not isinstance(resolved_endpoint, AdbServerEndpoint):
                raise TypeError(
                    "server backend start() must return AdbServerEndpoint"
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
                    self._backend.stop(resolved_endpoint)
                except BaseException as stop_error:
                    raise AdbServerStartError(
                        "ADB server identity creation failed and its native lifetime "
                        "could not be stopped"
                    ) from stop_error
                raise

            self._active_server = server
            return server

    def stop(self, server: AdbServer) -> None:
        """Synchronously stop the exact active domain server lifetime."""

        if not isinstance(server, AdbServer):
            raise TypeError("server must be AdbServer")

        with self._mutation_lock:
            if self._active_server != server:
                raise AdbServerStopError(
                    "no exact active ADB server lifetime is registered for the request"
                )

            self._backend.stop(server.endpoint)
            self._active_server = None


__all__ = ["AdbServerController"]
