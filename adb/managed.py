from __future__ import annotations

from threading import RLock

from adb.server.identity import AdbServer
from adb.transport.configuration import AdbConfiguredTransport


def _require_bool(value: object, *, field_name: str) -> bool:
    if not isinstance(value, bool):
        raise TypeError(f"{field_name} must be bool")
    return value


class RegisteredTransport:
    """Long-lived mutable handle for one runtime-scoped transport registration.

    The handle survives replacement of the current :class:`AdbServer` lifetime.  Resolution
    and recovery episodes do not: those are re-established from the current server/tracker
    scope.  The handle remains registered until explicitly removed or until its owning runtime
    is closed.
    """

    __slots__ = ("_runtime", "_configuration", "_recovery_enabled", "_is_registered")

    def __init__(
        self,
        runtime: AdbManagedRuntime,
        configuration: AdbConfiguredTransport,
        *,
        recovery_enabled: bool,
    ) -> None:
        self._runtime = runtime
        self._configuration = configuration
        self._recovery_enabled = recovery_enabled
        self._is_registered = True

    @property
    def configuration(self) -> AdbConfiguredTransport:
        """Server-independent configured transport owned by this registration."""

        return self._configuration

    @property
    def recovery_enabled(self) -> bool:
        """Whether observed disappearance may trigger automatic recovery."""

        with self._runtime._registration_lock:
            return self._recovery_enabled

    @property
    def is_registered(self) -> bool:
        """Whether this handle still belongs to its owning runtime."""

        with self._runtime._registration_lock:
            return self._is_registered

    def set_recovery_enabled(self, enabled: bool) -> None:
        """Mutate automatic-recovery intent for this registration.

        Changing this flag does not establish an absent transport immediately.  The new intent
        applies to future disappearance observations in the current or a later server/tracker
        scope while the registration remains active.
        """

        self._runtime._set_registration_recovery_enabled(self, enabled)

    def _mark_unregistered(self) -> None:
        self._is_registered = False


class AdbManagedRuntime:
    """Manage one ADB endpoint across successive server lifetimes.

    The runtime is the long-lived owner of configured-transport registrations.  ``server`` is
    only the current :class:`AdbServer` lifetime and may therefore be replaced, or become
    ``None`` while no usable server lifetime is active.  Registrations are independent of that
    replacement and persist until explicitly removed or until the runtime is closed.
    """

    def __init__(self, server: AdbServer) -> None:
        if not isinstance(server, AdbServer):
            raise TypeError("server must be AdbServer")
        self.server: AdbServer | None = server
        self.endpoint = server.endpoint
        self._registration_lock = RLock()
        self._registrations: dict[AdbConfiguredTransport, RegisteredTransport] = {}

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
    # Server lifecycle
    # ------------------------------------------------------------------

    def start_server(self, *, auto_recovery: bool = True) -> None:
        """Declare that this runtime endpoint should have an active ADB server lifetime."""
        raise NotImplementedError

    def stop_server(self) -> None:
        """Clear running intent without terminating the current ADB server."""
        raise NotImplementedError

    def set_server_auto_recovery(self, enabled: bool) -> None:
        """Enable or disable creation of a successor after the active server is retired."""
        raise NotImplementedError

    # ------------------------------------------------------------------
    # Transport registration lifecycle
    # ------------------------------------------------------------------

    def add_transport(
        self,
        configuration: AdbConfiguredTransport,
        *,
        recovery_enabled: bool = True,
    ) -> RegisteredTransport:
        """Add one runtime-scoped configured-transport registration.

        The returned handle is long-lived and survives successive ``AdbServer`` lifetimes.
        Adding a registration observes the current tracker baseline when one exists, but does
        not establish an already-absent transport.  Automatic recovery only applies to a
        disappearance observed after a resolved baseline.
        """

        if not isinstance(configuration, AdbConfiguredTransport):
            raise TypeError("configuration must be AdbConfiguredTransport")
        enabled = _require_bool(recovery_enabled, field_name="recovery_enabled")
        with self._registration_lock:
            if configuration in self._registrations:
                raise ValueError("ADB configured transport is already registered in this runtime")
            self._register_transport(configuration, recovery_enabled=enabled)
            registration = RegisteredTransport(
                self,
                configuration,
                recovery_enabled=enabled,
            )
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

    def _set_registration_recovery_enabled(
        self,
        transport: RegisteredTransport,
        enabled: bool,
    ) -> None:
        normalized = _require_bool(enabled, field_name="enabled")
        with self._registration_lock:
            registration = self._require_owned_registration_locked(transport)
            if registration._recovery_enabled is normalized:
                return
            self._update_transport_recovery_enabled(
                registration.configuration,
                enabled=normalized,
            )
            registration._recovery_enabled = normalized

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
        *,
        recovery_enabled: bool,
    ) -> None:
        """Concrete hook that starts supervising one runtime-scoped registration."""

        raise NotImplementedError

    def _unregister_transport(self, configuration: AdbConfiguredTransport) -> None:
        """Concrete hook that stops supervising one runtime-scoped registration."""

        raise NotImplementedError

    def _update_transport_recovery_enabled(
        self,
        configuration: AdbConfiguredTransport,
        *,
        enabled: bool,
    ) -> None:
        """Concrete hook that applies mutable recovery intent to one registration."""

        raise NotImplementedError


__all__ = ["AdbManagedRuntime", "RegisteredTransport"]
