from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
import errno
from enum import Enum
from math import isfinite
from numbers import Real
import socket
from typing import Any, Protocol, runtime_checkable

from adb._internal.framing import encode_service, parse_hex_length
from adb.errors import AdbProtocolError
from adb.server.endpoint.model import AdbServerEndpoint
from adb.server.status.model import AdbServerStatus


class EndpointObservationStatus(str, Enum):
    """Point-in-time evidence observed at one endpoint.

    None of these states promises what a later bind or connect will observe. In
    particular, ``NO_LISTENER_OBSERVED`` is deliberately not named ``FREE``.
    """

    NO_LISTENER_OBSERVED = "no_listener_observed"
    ADB_SERVER_VERIFIED = "adb_server_verified"
    ADB_SERVER_INCOMPATIBLE_OBSERVED = "adb_server_incompatible_observed"
    OTHER_LISTENER_OBSERVED = "other_listener_observed"
    LISTENER_OBSERVED_UNVERIFIED = "listener_observed_unverified"
    INDETERMINATE = "indeterminate"


def _normalize_optional_diagnostic(value: object) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise TypeError("endpoint observation diagnostic must be a string or None")
    normalized = value.strip()
    if not normalized:
        raise ValueError("endpoint observation diagnostic cannot be empty")
    return normalized


@dataclass(frozen=True, slots=True)
class EndpointObservation:
    """Structured evidence from one TCP plus ADB smart-socket probe."""

    endpoint: AdbServerEndpoint
    status: EndpointObservationStatus
    server_status: AdbServerStatus | None = None
    diagnostic: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.endpoint, AdbServerEndpoint):
            raise TypeError("endpoint must be AdbServerEndpoint")
        if not isinstance(self.status, EndpointObservationStatus):
            raise TypeError("status must be EndpointObservationStatus")
        if self.status is EndpointObservationStatus.ADB_SERVER_VERIFIED:
            if not isinstance(self.server_status, AdbServerStatus):
                raise ValueError("verified ADB observation requires AdbServerStatus")
        elif self.server_status is not None:
            raise ValueError("only a verified ADB observation may carry server_status")
        object.__setattr__(self, "diagnostic", _normalize_optional_diagnostic(self.diagnostic))

    @property
    def listener_observed(self) -> bool:
        return self.status in {
            EndpointObservationStatus.ADB_SERVER_VERIFIED,
            EndpointObservationStatus.ADB_SERVER_INCOMPATIBLE_OBSERVED,
            EndpointObservationStatus.OTHER_LISTENER_OBSERVED,
            EndpointObservationStatus.LISTENER_OBSERVED_UNVERIFIED,
        }

    @property
    def adb_speaking(self) -> bool:
        return self.status in {
            EndpointObservationStatus.ADB_SERVER_VERIFIED,
            EndpointObservationStatus.ADB_SERVER_INCOMPATIBLE_OBSERVED,
        }

    @property
    def adb_compatible(self) -> bool:
        return self.status is EndpointObservationStatus.ADB_SERVER_VERIFIED


@runtime_checkable
class AdbServerEndpointObserver(Protocol):
    """Perform one fresh endpoint observation."""

    def observe(self, endpoint: AdbServerEndpoint) -> EndpointObservation: ...


_Resolver = Callable[..., list[tuple[Any, ...]]]
_SocketFactory = Callable[[int, int, int], socket.socket]


class _UnexpectedEof(Exception):
    pass


def _normalize_timeout(value: object) -> float:
    if isinstance(value, bool) or not isinstance(value, Real):
        raise TypeError("endpoint observation timeout must be a real number")
    normalized = float(value)
    if not isfinite(normalized) or normalized <= 0.0:
        raise ValueError(
            "endpoint observation timeout must be finite and greater than zero"
        )
    return normalized


def _is_connection_refused(error: OSError) -> bool:
    return (
        isinstance(error, ConnectionRefusedError)
        or error.errno in {errno.ECONNREFUSED, 10061}
        or getattr(error, "winerror", None) == 10061
    )


class SmartSocketAdbServerEndpointObserver:
    """Classify one endpoint without treating a precheck as a bind guarantee.

    The implementation owns the socket phases instead of delegating to the general
    ADB client. This preserves whether failure happened before TCP connected or
    after a listener had already accepted the connection.
    """

    def __init__(
        self,
        timeout_seconds: float = 1.0,
        *,
        _resolver: _Resolver = socket.getaddrinfo,
        _socket_factory: _SocketFactory = socket.socket,
    ) -> None:
        self.timeout_seconds = _normalize_timeout(timeout_seconds)
        self._resolver = _resolver
        self._socket_factory = _socket_factory

    def observe(self, endpoint: AdbServerEndpoint) -> EndpointObservation:
        if not isinstance(endpoint, AdbServerEndpoint):
            raise TypeError("endpoint must be AdbServerEndpoint")

        try:
            addresses = self._resolver(
                endpoint.host,
                endpoint.port,
                type=socket.SOCK_STREAM,
            )
        except OSError as exc:
            return EndpointObservation(
                endpoint,
                EndpointObservationStatus.INDETERMINATE,
                diagnostic=f"failed to resolve endpoint: {exc}",
            )

        if not addresses:
            return EndpointObservation(
                endpoint,
                EndpointObservationStatus.INDETERMINATE,
                diagnostic="endpoint resolution returned no address candidates",
            )

        refused_diagnostics: list[str] = []
        indeterminate_diagnostics: list[str] = []
        for address in addresses:
            if len(address) < 5:
                indeterminate_diagnostics.append("resolver returned a malformed address")
                continue
            family, socktype, proto, _, sockaddr = address[:5]
            try:
                candidate = self._socket_factory(family, socktype, proto)
            except OSError as exc:
                indeterminate_diagnostics.append(f"failed to create socket: {exc}")
                continue

            try:
                candidate.settimeout(self.timeout_seconds)
                candidate.connect(sockaddr)
            except socket.timeout as exc:
                indeterminate_diagnostics.append(f"TCP connect timed out: {exc}")
            except OSError as exc:
                if _is_connection_refused(exc):
                    refused_diagnostics.append(f"TCP connection refused: {exc}")
                else:
                    indeterminate_diagnostics.append(f"TCP connect failed: {exc}")
            else:
                return self._probe_connected(endpoint, candidate)
            finally:
                self._close(candidate)

        if indeterminate_diagnostics:
            return EndpointObservation(
                endpoint,
                EndpointObservationStatus.INDETERMINATE,
                diagnostic="; ".join(indeterminate_diagnostics),
            )
        return EndpointObservation(
            endpoint,
            EndpointObservationStatus.NO_LISTENER_OBSERVED,
            diagnostic=(
                "; ".join(refused_diagnostics)
                if refused_diagnostics
                else "no TCP listener was observed"
            ),
        )

    def _probe_connected(
        self,
        endpoint: AdbServerEndpoint,
        sock: socket.socket,
    ) -> EndpointObservation:
        service = "host:server-status"
        try:
            sock.sendall(encode_service(service))
        except socket.timeout as exc:
            return self._listener_unverified(endpoint, f"ADB request send timed out: {exc}")
        except OSError as exc:
            return self._listener_unverified(endpoint, f"ADB request send failed: {exc}")

        try:
            status = self._recv_exact(sock, 4)
        except socket.timeout as exc:
            return self._listener_unverified(endpoint, f"ADB status read timed out: {exc}")
        except OSError as exc:
            return self._listener_unverified(endpoint, f"ADB status read failed: {exc}")
        except _UnexpectedEof:
            return self._listener_unverified(
                endpoint,
                "listener closed before an ADB status frame was received",
            )

        if status not in {b"OKAY", b"FAIL"}:
            return EndpointObservation(
                endpoint,
                EndpointObservationStatus.OTHER_LISTENER_OBSERVED,
                diagnostic=f"listener returned non-ADB status framing: {status!r}",
            )

        if status == b"FAIL":
            detail = self._read_protocol_detail(sock, context="ADB service error")
            suffix = f": {detail}" if detail else ""
            return EndpointObservation(
                endpoint,
                EndpointObservationStatus.ADB_SERVER_INCOMPATIBLE_OBSERVED,
                diagnostic=f"ADB server rejected host:server-status{suffix}",
            )

        try:
            # Imported lazily to keep endpoint value imports independent of the
            # broader transport inventory package graph.
            from adb._internal.proto import parse_server_status

            payload = self._read_protocol_payload(sock, context=service)
            server_status = parse_server_status(payload)
        except (AdbProtocolError, OSError, socket.timeout, _UnexpectedEof) as exc:
            return EndpointObservation(
                endpoint,
                EndpointObservationStatus.ADB_SERVER_INCOMPATIBLE_OBSERVED,
                diagnostic=f"ADB server-status response was not compatible: {exc}",
            )
        return EndpointObservation(
            endpoint,
            EndpointObservationStatus.ADB_SERVER_VERIFIED,
            server_status=server_status,
        )

    def _read_protocol_detail(self, sock: socket.socket, *, context: str) -> str | None:
        try:
            payload = self._read_protocol_payload(sock, context=context)
        except (AdbProtocolError, OSError, socket.timeout, _UnexpectedEof) as exc:
            return f"unreadable FAIL detail ({exc})"
        return payload.decode("utf-8", errors="replace").strip() or None

    def _read_protocol_payload(self, sock: socket.socket, *, context: str) -> bytes:
        raw_length = self._recv_exact(sock, 4)
        length = parse_hex_length(raw_length, context=context)
        return self._recv_exact(sock, length)

    @staticmethod
    def _recv_exact(sock: socket.socket, size: int) -> bytes:
        chunks: list[bytes] = []
        remaining = size
        while remaining:
            chunk = sock.recv(remaining)
            if not chunk:
                raise _UnexpectedEof("unexpected EOF from endpoint listener")
            chunks.append(chunk)
            remaining -= len(chunk)
        return b"".join(chunks)

    @staticmethod
    def _listener_unverified(
        endpoint: AdbServerEndpoint,
        diagnostic: str,
    ) -> EndpointObservation:
        return EndpointObservation(
            endpoint,
            EndpointObservationStatus.LISTENER_OBSERVED_UNVERIFIED,
            diagnostic=diagnostic,
        )

    @staticmethod
    def _close(sock: socket.socket) -> None:
        try:
            sock.close()
        except OSError:
            pass


__all__ = [
    "AdbServerEndpointObserver",
    "EndpointObservation",
    "EndpointObservationStatus",
    "SmartSocketAdbServerEndpointObserver",
]
