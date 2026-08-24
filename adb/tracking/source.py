from __future__ import annotations

from collections.abc import Iterator
from math import isfinite
from numbers import Real
import socket
from threading import Lock
from time import monotonic

from adb.tracking.model import AdbDevicesSnapshot
from adb._internal.framing import encode_service, parse_hex_length
from adb._internal.proto import parse_devices_snapshot
from adb.errors import (
    AdbProtocolError,
    AdbServerConnectionError,
    AdbServiceError,
)
from adb.server.identity import AdbServer


_SERVICE = "host:track-devices-proto-binary"


class _SourceClosed(Exception):
    pass


def _normalize_startup_timeout(value: object) -> float:
    if isinstance(value, bool) or not isinstance(value, Real):
        raise TypeError("startup_timeout_seconds must be a real number")
    timeout = float(value)
    if not isfinite(timeout) or timeout <= 0:
        raise ValueError("startup_timeout_seconds must be finite and greater than zero")
    return timeout


def _parse_snapshot(payload: bytes) -> AdbDevicesSnapshot:
    return parse_devices_snapshot(payload)


class AdbTrackDevicesSession:
    """One established blocking track-devices stream session."""

    def __init__(self, source: "AdbTrackDevicesSource", session_socket: socket.socket) -> None:
        self._source = source
        self._socket = session_socket
        self._closed = False

    def snapshots(self) -> Iterator[AdbDevicesSnapshot]:
        """Yield complete snapshots until the session closes or tracking fails."""

        if self._closed:
            return
        try:
            while True:
                yield _parse_snapshot(self._source._read_frame(self._socket))
        except _SourceClosed:
            return

    def close(self) -> None:
        """Close this stream session."""

        if self._closed:
            return
        self._closed = True
        self._source._release_session(self._socket)


class AdbTrackDevicesSource:
    """Blocking ``host:track-devices-proto-binary`` source.

    Payloads are AOSP ``adb_host.proto.Devices`` messages. The source does not retry or reconnect.
    """

    def __init__(
        self,
        server: AdbServer,
        startup_timeout_seconds: float = 5.0,
    ) -> None:
        if not isinstance(server, AdbServer):
            raise TypeError("server must be AdbServer")
        self.server = server
        self.endpoint = server.endpoint
        self.startup_timeout_seconds = _normalize_startup_timeout(
            startup_timeout_seconds
        )
        self._lock = Lock()
        self._closed = False
        self._session_active = False
        self._active_socket: socket.socket | None = None

    def close(self) -> None:
        """Permanently close the source and interrupt an active socket read."""

        active_socket: socket.socket | None
        with self._lock:
            if self._closed:
                return
            self._closed = True
            active_socket = self._active_socket

        if active_socket is not None:
            try:
                active_socket.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            try:
                active_socket.close()
            except OSError:
                pass

    def open(self) -> AdbTrackDevicesSession | None:
        """Establish one tracker stream and return after stream mode is entered."""

        if not self._acquire_session():
            return None

        session_socket: socket.socket | None = None
        try:
            session_socket, deadline = self._connect()
            self._handshake(session_socket, deadline)
            self._enter_stream_mode(session_socket)
            return AdbTrackDevicesSession(self, session_socket)
        except _SourceClosed:
            self._release_session(session_socket)
            return None
        except BaseException:
            self._release_session(session_socket)
            raise

    def _acquire_session(self) -> bool:
        with self._lock:
            if self._closed:
                return False
            if self._session_active:
                raise RuntimeError("an ADB tracking session is already active")
            self._session_active = True
            return True

    def _release_session(self, session_socket: socket.socket | None) -> None:
        if session_socket is not None:
            try:
                session_socket.close()
            except OSError:
                pass
        with self._lock:
            if self._active_socket is session_socket:
                self._active_socket = None
            self._session_active = False

    def _is_closed(self) -> bool:
        with self._lock:
            return self._closed

    def _register_socket(self, candidate: socket.socket) -> None:
        with self._lock:
            if self._closed:
                raise _SourceClosed
            self._active_socket = candidate

    def _unregister_socket(self, candidate: socket.socket) -> None:
        with self._lock:
            if self._active_socket is candidate:
                self._active_socket = None

    def _remaining_timeout(self, deadline: float) -> float:
        remaining = deadline - monotonic()
        if remaining <= 0:
            raise AdbServerConnectionError(
                "ADB track-devices startup timed out"
            )
        return remaining

    def _connect(self) -> tuple[socket.socket, float]:
        try:
            addresses = socket.getaddrinfo(
                self.endpoint.host,
                self.endpoint.port,
                type=socket.SOCK_STREAM,
            )
        except OSError as exc:
            if self._is_closed():
                raise _SourceClosed from exc
            raise AdbServerConnectionError(
                f"failed to resolve ADB server endpoint {self.endpoint.host!r}"
            ) from exc

        if self._is_closed():
            raise _SourceClosed

        # Synchronous hostname resolution above cannot be interrupted by a socket
        # timeout. The startup deadline begins after resolution and is shared by
        # all connect attempts plus the ADB service handshake.
        deadline = monotonic() + self.startup_timeout_seconds
        last_error: OSError | None = None
        for family, socktype, proto, _, sockaddr in addresses:
            try:
                candidate = socket.socket(family, socktype, proto)
            except OSError as exc:
                if self._is_closed():
                    raise _SourceClosed from exc
                last_error = exc
                continue
            try:
                self._register_socket(candidate)
                self._set_deadline_timeout(candidate, deadline)
                candidate.connect(sockaddr)
                return candidate, deadline
            except _SourceClosed:
                try:
                    candidate.close()
                except OSError:
                    pass
                raise
            except (OSError, AdbServerConnectionError) as exc:
                if self._is_closed():
                    self._unregister_socket(candidate)
                    try:
                        candidate.close()
                    except OSError:
                        pass
                    raise _SourceClosed from exc
                if isinstance(exc, OSError):
                    last_error = exc
                self._unregister_socket(candidate)
                try:
                    candidate.close()
                except OSError:
                    pass
                if isinstance(exc, AdbServerConnectionError):
                    raise

        detail = str(last_error) if last_error is not None else "no address candidates"
        raise AdbServerConnectionError(
            f"failed to connect to ADB server endpoint: {detail}"
        )

    def _set_deadline_timeout(self, sock: socket.socket, deadline: float) -> None:
        timeout = self._remaining_timeout(deadline)
        try:
            sock.settimeout(timeout)
        except OSError as exc:
            if self._is_closed():
                raise _SourceClosed from exc
            raise AdbServerConnectionError(
                "failed to configure ADB track-devices startup timeout"
            ) from exc

    def _enter_stream_mode(self, sock: socket.socket) -> None:
        try:
            sock.settimeout(None)
        except OSError as exc:
            if self._is_closed():
                raise _SourceClosed from exc
            raise AdbServerConnectionError(
                "failed to enter blocking ADB track-devices stream mode"
            ) from exc

    def _handshake(self, sock: socket.socket, deadline: float) -> None:
        request = encode_service(_SERVICE)
        self._send_all(sock, request, deadline)
        status = self._recv_exact(sock, 4, deadline=deadline)
        if status == b"OKAY":
            return
        if status == b"FAIL":
            length_raw = self._recv_exact(sock, 4, deadline=deadline)
            try:
                length = parse_hex_length(length_raw, context="service error")
            except AdbProtocolError as exc:
                raise AdbProtocolError(str(exc)) from exc
            detail_raw = self._recv_exact(sock, length, deadline=deadline)
            try:
                detail = detail_raw.decode("utf-8")
            except UnicodeDecodeError as exc:
                raise AdbProtocolError(
                    "ADB service error is not valid UTF-8"
                ) from exc
            raise AdbServiceError(
                _SERVICE,
                detail or "ADB server rejected track-devices",
            )
        raise AdbProtocolError(
            f"unexpected ADB service status: {status!r}"
        )

    def _read_frame(self, sock: socket.socket) -> bytes:
        length_raw = self._recv_exact(sock, 4, deadline=None)
        try:
            length = parse_hex_length(length_raw, context="snapshot")
        except AdbProtocolError as exc:
            raise AdbProtocolError(str(exc)) from exc
        return self._recv_exact(sock, length, deadline=None)

    def _send_all(self, sock: socket.socket, data: bytes, deadline: float) -> None:
        self._set_deadline_timeout(sock, deadline)
        try:
            sock.sendall(data)
        except socket.timeout as exc:
            if self._is_closed():
                raise _SourceClosed from exc
            raise AdbServerConnectionError(
                "ADB track-devices startup timed out"
            ) from exc
        except OSError as exc:
            if self._is_closed():
                raise _SourceClosed from exc
            raise AdbServerConnectionError(
                "failed to send ADB track-devices service request"
            ) from exc

    def _recv_exact(
        self,
        sock: socket.socket,
        size: int,
        *,
        deadline: float | None,
    ) -> bytes:
        chunks: list[bytes] = []
        remaining = size
        while remaining:
            if deadline is not None:
                self._set_deadline_timeout(sock, deadline)
            try:
                chunk = sock.recv(remaining)
            except socket.timeout as exc:
                if self._is_closed():
                    raise _SourceClosed from exc
                raise AdbServerConnectionError(
                    "ADB track-devices startup timed out"
                ) from exc
            except OSError as exc:
                if self._is_closed():
                    raise _SourceClosed from exc
                raise AdbServerConnectionError(
                    "ADB track-devices socket read failed"
                ) from exc
            if not chunk:
                if self._is_closed():
                    raise _SourceClosed
                raise AdbServerConnectionError(
                    "unexpected EOF from ADB track-devices stream"
                )
            chunks.append(chunk)
            remaining -= len(chunk)
        return b"".join(chunks)
