from __future__ import annotations

from collections.abc import Iterator
from math import isfinite
from numbers import Real
import socket
from threading import Lock
from time import monotonic
from typing import Protocol, runtime_checkable

from adb.aosp.tracking.model import Devices
from adb.aosp.protocol.smart_socket.framing import encode_service, parse_hex_length
from adb.aosp.tracking.decoder import parse_devices
from adb.aosp.errors import (
    AdbProtocolError,
    AdbServerConnectionError,
    AdbServiceError,
)
from adb.aosp.server.address import AdbServerTcpAddress


_SERVICE = "host:track-devices-proto-binary"


class _TrackingBackendClosed(Exception):
    pass


def _normalize_startup_timeout(value: object) -> float:
    if isinstance(value, bool) or not isinstance(value, Real):
        raise TypeError("startup_timeout_seconds must be a real number")
    timeout = float(value)
    if not isfinite(timeout) or timeout <= 0:
        raise ValueError("startup_timeout_seconds must be finite and greater than zero")
    return timeout


def _parse_record(payload: bytes) -> Devices:
    return parse_devices(payload)


@runtime_checkable
class AdbDevicesTrackingBackendStream(Protocol):
    """Established backend stream yielding complete tracked-device records."""

    @property
    def initial_record(self) -> Devices:
        ...

    def records(self) -> Iterator[Devices]:
        ...

    def close(self) -> None:
        ...


@runtime_checkable
class AdbDevicesTrackingBackend(Protocol):
    """Backend port for one server-bound ADB devices-tracking attachment.

    Backends establish and release the low-level tracking stream only. They do not retry,
    reconnect, issue snapshot identities, publish events, or supervise server replacement.
    """

    @property
    def address(self) -> AdbServerTcpAddress:
        ...

    def open(self) -> AdbDevicesTrackingBackendStream | None:
        """Establish one stream and synchronously obtain its initial complete record."""
        ...

    def close(self) -> None:
        """Release the backend attachment and interrupt any active stream read."""
        ...


class SmartSocketAdbDevicesTrackingStream:
    """One established blocking ADB device-tracker stream.

    ``initial_record`` is the first complete record read while the tracker is still under its
    startup deadline. ``records`` yields only subsequent stream observations.
    """

    def __init__(
        self,
        backend: "SmartSocketAdbDevicesTrackingBackend",
        stream_socket: socket.socket,
        initial_record: Devices,
    ) -> None:
        if not isinstance(initial_record, Devices):
            raise TypeError("initial_record must be Devices")
        self._backend = backend
        self._socket = stream_socket
        self.initial_record = initial_record
        self._closed = False

    def records(self) -> Iterator[Devices]:
        """Yield complete records until the stream closes or tracking fails."""

        if self._closed:
            return
        try:
            while True:
                yield _parse_record(self._backend._read_frame(self._socket))
        except _TrackingBackendClosed:
            return

    def close(self) -> None:
        """Close this device-tracker stream."""

        if self._closed:
            return
        self._closed = True
        self._backend._release_stream(self._socket)


class SmartSocketAdbDevicesTrackingBackend:
    """Smart-socket implementation of the devices-tracking backend port.

    Payloads are AOSP ``adb_host.proto.Devices`` messages. The backend does not retry or
    reconnect; lifecycle policy belongs to higher layers.
    """

    def __init__(
        self,
        address: AdbServerTcpAddress,
        startup_timeout_seconds: float = 5.0,
    ) -> None:
        if not isinstance(address, AdbServerTcpAddress):
            raise TypeError("address must be AdbServerTcpAddress")
        self.address = address
        self.startup_timeout_seconds = _normalize_startup_timeout(
            startup_timeout_seconds
        )
        self._lock = Lock()
        self._closed = False
        self._stream_active = False
        self._active_socket: socket.socket | None = None

    def close(self) -> None:
        """Permanently close the tracker and interrupt an active socket read."""

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

    def open(self) -> SmartSocketAdbDevicesTrackingStream | None:
        """Establish one tracker stream and synchronously read its initial record."""

        if not self._acquire_stream():
            return None

        stream_socket: socket.socket | None = None
        try:
            stream_socket, deadline = self._connect()
            self._handshake(stream_socket, deadline)
            initial_record = _parse_record(
                self._read_frame(stream_socket, deadline=deadline)
            )
            self._enter_stream_mode(stream_socket)
            return SmartSocketAdbDevicesTrackingStream(
                self,
                stream_socket,
                initial_record,
            )
        except _TrackingBackendClosed:
            self._release_stream(stream_socket)
            return None
        except BaseException:
            self._release_stream(stream_socket)
            raise

    def _acquire_stream(self) -> bool:
        with self._lock:
            if self._closed:
                return False
            if self._stream_active:
                raise RuntimeError("an ADB device tracker stream is already active")
            self._stream_active = True
            return True

    def _release_stream(self, stream_socket: socket.socket | None) -> None:
        if stream_socket is not None:
            try:
                stream_socket.close()
            except OSError:
                pass
        with self._lock:
            if self._active_socket is stream_socket:
                self._active_socket = None
            self._stream_active = False

    def _is_closed(self) -> bool:
        with self._lock:
            return self._closed

    def _register_socket(self, candidate: socket.socket) -> None:
        with self._lock:
            if self._closed:
                raise _TrackingBackendClosed
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
                self.address.host,
                self.address.port,
                type=socket.SOCK_STREAM,
            )
        except OSError as exc:
            if self._is_closed():
                raise _TrackingBackendClosed from exc
            raise AdbServerConnectionError(
                f"failed to resolve ADB server address {self.address.host!r}"
            ) from exc

        if self._is_closed():
            raise _TrackingBackendClosed

        # Synchronous hostname resolution above cannot be interrupted by a socket
        # timeout. The startup deadline begins after resolution and is shared by
        # all connect attempts, the ADB service handshake, and the first complete snapshot.
        deadline = monotonic() + self.startup_timeout_seconds
        last_error: OSError | None = None
        for family, socktype, proto, _, sockaddr in addresses:
            try:
                candidate = socket.socket(family, socktype, proto)
            except OSError as exc:
                if self._is_closed():
                    raise _TrackingBackendClosed from exc
                last_error = exc
                continue
            try:
                self._register_socket(candidate)
                self._set_deadline_timeout(candidate, deadline)
                candidate.connect(sockaddr)
                return candidate, deadline
            except _TrackingBackendClosed:
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
                    raise _TrackingBackendClosed from exc
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
            f"failed to connect to ADB server address: {detail}"
        )

    def _set_deadline_timeout(self, sock: socket.socket, deadline: float) -> None:
        timeout = self._remaining_timeout(deadline)
        try:
            sock.settimeout(timeout)
        except OSError as exc:
            if self._is_closed():
                raise _TrackingBackendClosed from exc
            raise AdbServerConnectionError(
                "failed to configure ADB track-devices startup timeout"
            ) from exc

    def _enter_stream_mode(self, sock: socket.socket) -> None:
        try:
            sock.settimeout(None)
        except OSError as exc:
            if self._is_closed():
                raise _TrackingBackendClosed from exc
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

    def _read_frame(
        self,
        sock: socket.socket,
        *,
        deadline: float | None = None,
    ) -> bytes:
        length_raw = self._recv_exact(sock, 4, deadline=deadline)
        try:
            length = parse_hex_length(length_raw, context="snapshot")
        except AdbProtocolError as exc:
            raise AdbProtocolError(str(exc)) from exc
        return self._recv_exact(sock, length, deadline=deadline)

    def _send_all(self, sock: socket.socket, data: bytes, deadline: float) -> None:
        self._set_deadline_timeout(sock, deadline)
        try:
            sock.sendall(data)
        except socket.timeout as exc:
            if self._is_closed():
                raise _TrackingBackendClosed from exc
            raise AdbServerConnectionError(
                "ADB track-devices startup timed out"
            ) from exc
        except OSError as exc:
            if self._is_closed():
                raise _TrackingBackendClosed from exc
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
                    raise _TrackingBackendClosed from exc
                raise AdbServerConnectionError(
                    "ADB track-devices startup timed out"
                ) from exc
            except OSError as exc:
                if self._is_closed():
                    raise _TrackingBackendClosed from exc
                raise AdbServerConnectionError(
                    "ADB track-devices socket read failed"
                ) from exc
            if not chunk:
                if self._is_closed():
                    raise _TrackingBackendClosed
                raise AdbServerConnectionError(
                    "unexpected EOF from ADB track-devices stream"
                )
            chunks.append(chunk)
            remaining -= len(chunk)
        return b"".join(chunks)


__all__ = [
    "AdbDevicesTrackingBackend",
    "AdbDevicesTrackingBackendStream",
    "SmartSocketAdbDevicesTrackingBackend",
    "SmartSocketAdbDevicesTrackingStream",
]
