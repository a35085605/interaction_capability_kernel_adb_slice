"""Public runtime acquisition API for host-side ADB capabilities."""

from __future__ import annotations

from adb.bootstrap import AdbRuntimeBootstrap as _InternalAdbRuntimeBootstrap
from adb.managed import RegisteredTransport as _RegisteredTransport
from adb.runtime import AdbRuntime as _InternalAdbRuntime

# Load the internal runtime graph before the public transport boundary to preserve package
# initialization order.
from adb.api.transport import (
    AdbConfiguredTransportRegistration,
    _configured_transport_from_registration,
)
from adb.server.endpoint import AdbServerEndpoint


class AdbConfiguredTransportHandle:
    """Runtime-bound live handle for one public configured-transport registration."""

    __slots__ = ("_runtime", "_registration", "_transport")

    def __init__(
        self,
        runtime: AdbRuntime,
        registration: AdbConfiguredTransportRegistration,
        transport: _RegisteredTransport,
    ) -> None:
        self._runtime = runtime
        self._registration = registration
        self._transport = transport

    @property
    def registration(self) -> AdbConfiguredTransportRegistration:
        return self._registration

    @property
    def is_registered(self) -> bool:
        return self._transport.is_registered


class AdbRuntime(_InternalAdbRuntime):
    """Public ADB runtime facade with declarative transport registration."""

    def add_transport(
        self,
        registration: AdbConfiguredTransportRegistration,
    ) -> AdbConfiguredTransportHandle:
        """Register one declarative transport and return its runtime-bound handle."""

        if not isinstance(registration, AdbConfiguredTransportRegistration):
            raise TypeError("registration must be AdbConfiguredTransportRegistration")
        configuration = _configured_transport_from_registration(registration)
        transport = super().add_transport(configuration, registration.policy)
        return AdbConfiguredTransportHandle(self, registration, transport)

    def remove_transport(self, transport: AdbConfiguredTransportHandle) -> None:
        """Remove one runtime-bound configured-transport registration."""

        if not isinstance(transport, AdbConfiguredTransportHandle):
            raise TypeError("transport must be AdbConfiguredTransportHandle")
        if transport._runtime is not self:
            raise ValueError("configured transport handle belongs to a different runtime")
        super().remove_transport(transport._transport)


class AdbRuntimeBootstrap(_InternalAdbRuntimeBootstrap):
    """Public composition root that builds :class:`AdbRuntime` instances."""

    def _build_runtime(self, *args: object, **kwargs: object) -> AdbRuntime:
        return AdbRuntime(*args, **kwargs)


__all__ = [
    "AdbConfiguredTransportHandle",
    "AdbRuntime",
    "AdbRuntimeBootstrap",
    "AdbServerEndpoint",
]
