from __future__ import annotations

from typing import Protocol, runtime_checkable

from networking import TcpAddress
from adb.transport_list.session_identity import AdbTransportListSessionIdentityIssuer
from adb.transport_list.watch.session import AdbTransportListWatchSession


@runtime_checkable
class AdbTransportListWatchBackend(Protocol):
    """Synchronously establish at most one owned watch session at a time.

    Calls to open, session reads, and session close must be serialized by the caller.
    Startup cannot be cancelled; interrupting a blocked read from another thread is
    outside this contract. Close the current session before opening another one.

    A returned session has a stable identity and a parsed, complete initial list
    (which may be empty). Its updates are a single-consumer stream of subsequent
    complete lists, without replay or automatic reconnection. Repeated updates()
    calls access the same iterator.

    The session owns its resources. Close is terminal and idempotent; reads after
    close end without I/O. Read failure closes the session before propagating the
    error. Consumers that stop iteration early must still explicitly close it.
    Neither opening nor closing grants or revokes domain observation authority.
    """

    def open(
        self,
        endpoint: TcpAddress,
        *,
        startup_timeout_seconds: float = 5.0,
    ) -> AdbTransportListWatchSession:
        """Return a fully established session, or clean up and raise an error.

        Expected establishment failures raise AdbTransportListWatchError; there is
        no cancellation/None result. An already owned session causes RuntimeError
        without changing it. Programming and argument errors propagate unchanged.
        """
        ...


class AdbTransportListWatchBackendFactory(Protocol):
    """Construct a backend using the externally owned runtime-scoped issuer."""

    def __call__(
        self,
        identity_issuer: AdbTransportListSessionIdentityIssuer,
    ) -> AdbTransportListWatchBackend:
        ...


__all__ = [
    "AdbTransportListWatchBackend",
    "AdbTransportListWatchBackendFactory",
]
