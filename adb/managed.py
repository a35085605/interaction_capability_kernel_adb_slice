from __future__ import annotations

from threading import RLock

from adb.server.endpoint import AdbServerEndpoint
from adb.server.identity import AdbServer
from adb.server.state import AdbServerState, AdbServerStateView
from adb.transport.configuration import AdbConfiguredTransport
from adb.transport.lifecycle.supervision.policy import (
    AdbConfiguredTransportSupervisionPolicy,
)


class RegisteredTransport:
    """Long-lived handle for one runtime-scoped transport registration.

    The handle survives replacement of the current :class:`AdbServer` lifetime.  Resolution
    and recovery episodes do not: those are re-established from the current server/tracker
    scope.  The handle remains registered until explicitly removed or until its owning runtime
    is closed.
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
        """Server-independent configured transport owned by this registration."""

        return self._configuration

    @property
    def is_registered(self) -> bool:
        """Whether this handle still belongs to its owning runtime."""

        with self._runtime._registration_lock:
            return self._is_registered

    def _mark_unregistered(self) -> None:
        self._is_registered = False


class AdbManagedRuntime:
    """Manage successive ADB server lifetimes within one runtime.

    The runtime is the long-lived owner of configured-transport registrations.  ``server`` is
    only the current :class:`AdbServer` lifetime and may therefore be replaced, move to a
    different endpoint, or become ``None`` while no usable server lifetime is active.
    Registrations are independent of those replacements and persist until explicitly removed
    or until the runtime is closed.
    """

    def __init__(self, server: AdbServer | AdbServerState) -> None:
        if isinstance(server, AdbServerState):
            server_state = server
        elif isinstance(server, AdbServer):
            server_state = AdbServerState(server)
        else:
            raise TypeError("server must be AdbServer or AdbServerState")
        self._server_state = server_state
        self._registration_lock = RLock()
        self._registrations: dict[AdbConfiguredTransport, RegisteredTransport] = {}

    @property
    def server(self) -> AdbServer | None:
        """Authoritative current ADB server lifetime for this runtime."""

        return self._server_state.current

    @property
    def server_state(self) -> AdbServerStateView:
        """Read-only authoritative server-state projection for this runtime."""

        return self._server_state

    @property
    def current_endpoint(self) -> AdbServerEndpoint | None:
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
        """Release runtime resources without stopping the current ADB server.

        Concrete implementations must call :meth:`_close_transport_registrations` before
        returning so all runtime-scoped handles become inactive.
        """
        raise NotImplementedError

    # ------------------------------------------------------------------
    # Transport registration lifecycle
    # ------------------------------------------------------------------

    def add_transport(
        self,
        configuration: AdbConfiguredTransport,
        policy: AdbConfiguredTransportSupervisionPolicy | None = None,
    ) -> RegisteredTransport:
        """Add one runtime-scoped configured-transport registration.

        The returned handle is long-lived and survives successive ``AdbServer`` lifetimes.
        When an active tracker already has a current observation, adding a registration projects
        that observation immediately. An absent configured TCP transport is reconciled through
        automatic recovery when enabled; USB remains projection-only.
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
        """Remove one runtime-scoped registration permanently.

        Removal invalidates any in-flight recovery result associated with the registration.
        A later server replacement must not recreate the removed registration.
        """

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
