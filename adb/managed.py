from __future__ import annotations

from typing import Protocol

from adb.server.identity import AdbServer
from adb.transport.configuration import AdbConfiguredTransport


class RegisteredTransport(Protocol):
    """One configured transport registration managed by the ADB runtime."""

    def set_disappearance_recovery_enabled(self, enabled: bool) -> None:
        """Toggle recovery after an observed transport disappearance."""
        ...


class AdbManagedRuntime:
    """Managed lifecycle rooted in one process-coordinated ADB-owned server lifetime.

    Managed composition deliberately does not accept a bare ``AdbServerEndpoint``. The
    :class:`AdbServer` must originate from the process-coordinated ADB ownership store.
    Resource-bound children are expected to be destroyed when that server is retired
    and recreated only after a fresh server is acquired.
    """

    def __init__(self, server: AdbServer) -> None:
        if not isinstance(server, AdbServer):
            raise TypeError("server must be AdbServer")
        self.server = server
        self.endpoint = server.endpoint

    # ------------------------------------------------------------------
    # Runtime lifecycle
    # ------------------------------------------------------------------

    def start(self) -> None:
        """Start the managed runtime infrastructure."""
        raise NotImplementedError

    def close(self) -> None:
        """Stop managing runtime resources without terminating the process ADB server."""
        raise NotImplementedError

    # ------------------------------------------------------------------
    # Server lifecycle
    # ------------------------------------------------------------------

    def start_server(self, *, auto_recovery: bool = True) -> None:
        """Establish managed running intent around the owned server."""
        raise NotImplementedError

    def stop_server(self) -> None:
        """Disarm managed server intent without issuing native termination."""
        raise NotImplementedError

    def set_server_auto_recovery(self, enabled: bool) -> None:
        """Enable or disable recreation after server ownership is invalidated."""
        raise NotImplementedError

    # ------------------------------------------------------------------
    # Transport registration lifecycle
    # ------------------------------------------------------------------

    def add_transport(
        self,
        configuration: AdbConfiguredTransport,
        *,
        recover_on_disappearance: bool = True,
    ) -> RegisteredTransport:
        """Register transport tracking and optional post-resolution disappearance recovery.

        Registration does not establish a transport that is absent from its first observed
        snapshot. Initial presence/readiness establishment is a separate explicit operation.
        """
        raise NotImplementedError

    def remove_transport(self, transport: RegisteredTransport) -> None:
        """Release one managed transport registration."""
        raise NotImplementedError


__all__ = ["AdbManagedRuntime", "RegisteredTransport"]
