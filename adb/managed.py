from __future__ import annotations

from typing import Protocol

from adb.server.ownership import AdbServerRef
from adb.transport.configuration import AdbConfiguredTransport


class RegisteredTransport(Protocol):
    """One configured transport registration managed by the ADB runtime."""

    def set_disappearance_recovery_enabled(self, enabled: bool) -> None:
        """Toggle recovery after a transport disappears within one tracking generation."""
        ...


class AdbManagedRuntime:
    """Managed lifecycle bound to the process-owned ADB server reference.

    Managed composition deliberately does not accept a bare ``AdbServerEndpoint``.
    The server reference must originate from the process-level ownership slot, so
    endpoint observations alone cannot bootstrap managed ownership.
    """

    def __init__(self, server: AdbServerRef) -> None:
        if not isinstance(server, AdbServerRef):
            raise TypeError("server must be AdbServerRef")
        if not server.active:
            raise ValueError("server reference must be active")
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
        """Establish the server running condition."""
        raise NotImplementedError

    def stop_server(self) -> None:
        """Establish the server stopped condition."""
        raise NotImplementedError

    def set_server_auto_recovery(self, enabled: bool) -> None:
        """Enable or disable maintenance of the server running condition."""
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
