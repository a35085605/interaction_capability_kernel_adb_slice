from __future__ import annotations

from typing import Protocol

from adb.server.identity import AdbServer
from adb.transport.configuration import AdbConfiguredTransport


class RegisteredTransport(Protocol):
    """Registration for one configured transport."""

    def set_disappearance_recovery_enabled(self, enabled: bool) -> None:
        """Toggle recovery after an observed transport disappearance."""
        ...


class AdbManagedRuntime:
    """Manage ADB server and transport lifecycle for one server identity."""

    def __init__(self, server: AdbServer) -> None:
        if not isinstance(server, AdbServer):
            raise TypeError("server must be AdbServer")
        self.server = server
        self.endpoint = server.endpoint

    # ------------------------------------------------------------------
    # Runtime lifecycle
    # ------------------------------------------------------------------

    def start(self) -> None:
        """Start runtime infrastructure."""
        raise NotImplementedError

    def close(self) -> None:
        """Release runtime resources without stopping the current ADB server."""
        raise NotImplementedError

    # ------------------------------------------------------------------
    # Server lifecycle
    # ------------------------------------------------------------------

    def start_server(self, *, auto_recovery: bool = True) -> None:
        """Declare that the ADB server should be running."""
        raise NotImplementedError

    def stop_server(self) -> None:
        """Clear running intent without terminating the ADB server."""
        raise NotImplementedError

    def set_server_auto_recovery(self, enabled: bool) -> None:
        """Enable or disable recreation after the active server is retired."""
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
        """Register transport tracking and optional disappearance recovery.

        Registration does not establish an absent transport.
        """
        raise NotImplementedError

    def remove_transport(self, transport: RegisteredTransport) -> None:
        """Remove one transport registration."""
        raise NotImplementedError


__all__ = ["AdbManagedRuntime", "RegisteredTransport"]
