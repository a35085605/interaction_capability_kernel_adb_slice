from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, TypeAlias, runtime_checkable

from networking import TcpAddress
from adb.transport_list.session_identity import (
    AdbTransportListSessionIdentity,
    AdbTransportListSessionIdentityIssuer,
)
from adb.transport_list.watch.failure import AdbTransportListWatchFailure
from adb.transport_list.watch.session import AdbTransportListWatchSession


@dataclass(frozen=True, slots=True)
class AdbTransportListWatchBackendOpened:
    """Evidence that this call established and retained one usable watch session."""

    session: AdbTransportListWatchSession

    def __post_init__(self) -> None:
        if not isinstance(self.session, AdbTransportListWatchSession):
            raise TypeError("session must satisfy AdbTransportListWatchSession")

    @property
    def identity(self) -> AdbTransportListSessionIdentity:
        return self.session.session_identity


@dataclass(frozen=True, slots=True)
class AdbTransportListWatchBackendAlreadyOpen:
    """Evidence that the backend already owns a watch session."""


@dataclass(frozen=True, slots=True)
class AdbTransportListWatchBackendOpenFailed:
    """Expected failure to establish a usable watch session."""

    failure: AdbTransportListWatchFailure

    def __post_init__(self) -> None:
        if not isinstance(self.failure, AdbTransportListWatchFailure):
            raise TypeError("failure must be AdbTransportListWatchFailure")


AdbTransportListWatchBackendOpenResult: TypeAlias = (
    AdbTransportListWatchBackendOpened
    | AdbTransportListWatchBackendAlreadyOpen
    | AdbTransportListWatchBackendOpenFailed
)


@runtime_checkable
class AdbTransportListWatchBackend(Protocol):
    """Manage one runtime-scoped acquisition of transport-list watch resources.

    Calls to ``open``, session reads, and ``close`` are serialized by the caller.
    Startup cannot be cancelled; interrupting a blocked read from another thread is
    outside this contract.

    An opened session has a stable identity and a parsed, complete initial list
    (which may be empty). Its updates are a single-consumer stream of subsequent
    complete lists, without replay or automatic reconnection. Repeated ``updates()``
    calls access the same iterator.

    The backend owns only watch resources. Opening or closing does not grant or revoke
    authoritative watch-session state or transport-list observation authority.
    """

    def open(
        self,
        endpoint: TcpAddress,
        *,
        startup_timeout_seconds: float = 5.0,
    ) -> AdbTransportListWatchBackendOpenResult:
        """Attempt to establish one fully usable watch session.

        Expected ADB connection, service, and protocol establishment failures are
        returned as ``AdbTransportListWatchBackendOpenFailed``. An existing backend
        acquisition returns ``AdbTransportListWatchBackendAlreadyOpen``. Programming
        and argument errors propagate unchanged.
        """
        ...

    def close(self, expected: AdbTransportListSessionIdentity) -> bool:
        """Close the owned watch session when its identity matches ``expected``.

        Returns whether matching backend ownership was closed. A missing or different
        session is left untouched.
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
    "AdbTransportListWatchBackendAlreadyOpen",
    "AdbTransportListWatchBackendFactory",
    "AdbTransportListWatchBackendOpened",
    "AdbTransportListWatchBackendOpenFailed",
    "AdbTransportListWatchBackendOpenResult",
]
