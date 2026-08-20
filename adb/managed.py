from __future__ import annotations

from typing import Protocol

from adb.server.endpoint import AdbServerEndpoint
from adb.transport.configuration import AdbConfiguredTransport


class RegisteredTransport(Protocol):
    """One configured transport registration managed by the ADB runtime."""

    def set_disappearance_recovery_enabled(self, enabled: bool) -> None:
        """Toggle recovery after a transport disappears within one tracking generation."""
        ...


class AdbManagedRuntime:
    """Managed lifecycle for one ADB server endpoint and its registered transports."""

    def __init__(self, endpoint: AdbServerEndpoint) -> None:
        if not isinstance(endpoint, AdbServerEndpoint):
            raise TypeError("endpoint must be AdbServerEndpoint")
        self.endpoint = endpoint

    # ------------------------------------------------------------------
    # Runtime lifecycle
    # ------------------------------------------------------------------

    def start(self) -> None:
        """Start the managed runtime infrastructure."""
        raise NotImplementedError

    def close(self) -> None:
        """Stop managing runtime state and release runtime resources."""
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
