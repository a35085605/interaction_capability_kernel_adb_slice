from __future__ import annotations

from threading import RLock

from networking import TcpAddress
from adb.server.lifetime import AdbServerLifetime
from adb.server.state import AdbServerState, AdbServerStateView
from adb.transport.configuration import AdbConfiguredTransport
from adb.transport.lifecycle.supervision.policy import (
    AdbConfiguredTransportSupervisionPolicy,
)


class RegisteredTransport:
    """Runtime-scoped configured transport handle spanning server lifetimes while resolution and
    recovery remain server-scoped.
    """

    __slots__ = ("_runtime", "_configuration", "_is_registered")

    def __init__(
        self,
        runtime: AdbManagedRuntime,
        configuration: AdbConfiguredTransport,
    ) -> None:
        self._runtime = runtime
        self._configuration = configuration
        self._is_registered = True

    @property
    def configuration(self) -> AdbConfiguredTransport:
        """Configured transport owned by this runtime registration."""

        return self._configuration

    @property
    def is_registered(self) -> bool:
        """Whether this handle still belongs to its owning runtime."""

        with self._runtime._registration_lock:
            return self._is_registered

    def _mark_unregistered(self) -> None:
        self._is_registered = False


class AdbManagedRuntime:
    """Own runtime-scoped configured transport registrations across successive ADB server
    lifetimes.
    """

    def __init__(
        self,
        server: AdbServerLifetime | AdbServerState,
    ) -> None:
        if isinstance(server, AdbServerState):
            server_state = server
        elif isinstance(server, AdbServerLifetime):
            server_state = AdbServerState(server)
        else:
            raise TypeError("server must be AdbServerLifetime or AdbServerState")
        self._server_state = server_state
        self._registration_lock = RLock()
        self._registrations: dict[AdbConfiguredTransport, RegisteredTransport] = {}

    @property
    def server(self) -> AdbServerLifetime | None:
        """Authoritative current ADB server lifetime for this runtime."""

        return self._server_state.current

    @property
    def server_state(self) -> AdbServerStateView:
        """Authoritative server-state view for this runtime."""

        return self._server_state

    @property
    def current_endpoint(self) -> TcpAddress | None:
        """Endpoint of the current server lifetime, if one is active."""

        server = self.server
        return None if server is None else server.endpoint

    # ------------------------------------------------------------------
    # Runtime lifecycle
    # ------------------------------------------------------------------

    def start(self) -> None:
        """Start runtime infrastructure."""
        raise NotImplementedError

    def close(self) -> None:
        """Release runtime resources, invalidate handles, and preserve the current server."""
        raise NotImplementedError

    # ------------------------------------------------------------------
    # Transport registration lifecycle
    # ------------------------------------------------------------------

    def add_transport(
        self,
        configuration: AdbConfiguredTransport,
        policy: AdbConfiguredTransportSupervisionPolicy | None = None,
    ) -> RegisteredTransport:
        """Register one transport and return its runtime-scoped handle, projecting current
        transport-list evidence and optional TCP recovery.
        """

        if not isinstance(configuration, AdbConfiguredTransport):
            raise TypeError("configuration must be AdbConfiguredTransport")
        if policy is not None and not isinstance(
            policy, AdbConfiguredTransportSupervisionPolicy
        ):
            raise TypeError(
                "policy must be AdbConfiguredTransportSupervisionPolicy or None"
            )
        with self._registration_lock:
            if configuration in self._registrations:
                raise ValueError("ADB configured transport is already registered in this runtime")
            self._register_transport(configuration, policy)
            registration = RegisteredTransport(self, configuration)
            self._registrations[configuration] = registration
            return registration

    def remove_transport(self, transport: RegisteredTransport) -> None:
        """Remove one registration and invalidate its in-flight recovery work."""

        with self._registration_lock:
            registration = self._require_owned_registration_locked(transport)
            self._unregister_transport(registration.configuration)
            self._registrations.pop(registration.configuration)
            registration._mark_unregistered()

    def _require_owned_registration_locked(
        self,
        transport: RegisteredTransport,
    ) -> RegisteredTransport:
        if not isinstance(transport, RegisteredTransport):
            raise TypeError("transport must be RegisteredTransport")
        if transport._runtime is not self:
            raise ValueError("registered transport belongs to a different runtime")
        current = self._registrations.get(transport.configuration)
        if current is not transport or not transport._is_registered:
            raise RuntimeError("registered transport is no longer active")
        return transport

    def _close_transport_registrations(self) -> None:
        """Invalidate all registration handles after concrete supervision has been closed."""

        with self._registration_lock:
            registrations = tuple(self._registrations.values())
            self._registrations.clear()
            for registration in registrations:
                registration._mark_unregistered()

    def _register_transport(
        self,
        configuration: AdbConfiguredTransport,
        policy: AdbConfiguredTransportSupervisionPolicy | None,
    ) -> None:
        """Concrete hook that starts supervising one runtime-scoped registration."""

        raise NotImplementedError

    def _unregister_transport(self, configuration: AdbConfiguredTransport) -> None:
        """Concrete hook that stops supervising one runtime-scoped registration."""

        raise NotImplementedError


__all__ = ["AdbManagedRuntime", "RegisteredTransport"]
