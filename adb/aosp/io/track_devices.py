from __future__ import annotations

from collections.abc import Callable, Iterator
from math import isfinite
from numbers import Real
import socket
from threading import Event, Lock
from time import monotonic

from adb._lifecycle import ResourceEntry, ResourceScope
from adb._resolution import AddressResolutionCancelled, DeadlineResolver
from adb.aosp.model.track_devices import Devices, parse_devices
from adb.aosp.protocol.smart_socket.framing import encode_service, parse_hex_length
from adb.aosp.protocol.smart_socket.services import TRACK_DEVICES_PROTO_BINARY_SERVICE
from adb.errors import (
    AdbProtocolError,
    AdbServerConnectionError,
    AdbServiceError,
    AdbTimeoutError,
)
from networking import TcpAddress


_Clock = Callable[[], float]
_Resolver = Callable[..., list[tuple]]
_SocketFactory = Callable[..., socket.socket]


class AospTrackDevicesOpenCancelled(RuntimeError):
    """Opening an AOSP track-devices stream was cooperatively cancelled."""


def _normalize_startup_timeout(value: object) -> float:
    if isinstance(value, bool) or not isinstance(value, Real):
        raise TypeError("startup_timeout_seconds must be a real number")
    timeout = float(value)
    if not isfinite(timeout) or timeout <= 0:
        raise ValueError("startup_timeout_seconds must be finite and greater than zero")
    return timeout


def _check_cancelled(cancellation: Event | None) -> None:
    if cancellation is not None and cancellation.is_set():
        raise AospTrackDevicesOpenCancelled


def _close_socket(sock: socket.socket) -> bool:
    try:
        sock.close()
    except BaseException:
        return False
    return True


def _set_deadline_timeout(sock: socket.socket, deadline: float, clock: _Clock) -> None:
    remaining = deadline - clock()
    if remaining <= 0:
        raise AdbTimeoutError("ADB track-devices startup timed out")
    sock.settimeout(remaining)


def _recv_exact(
    sock: socket.socket,
    size: int,
    *,
    deadline: float | None = None,
    clock: _Clock = monotonic,
) -> bytes:
    chunks: list[bytes] = []
    remaining = size
    while remaining:
        if deadline is not None:
            _set_deadline_timeout(sock, deadline, clock)
        chunk = sock.recv(remaining)
        if not chunk:
            raise AdbServerConnectionError("unexpected EOF from ADB track-devices stream")
        chunks.append(chunk)
        remaining -= len(chunk)
    return b"".join(chunks)


def _read_frame(
    sock: socket.socket,
    *,
    context: str = "track-devices record",
    deadline: float | None = None,
    clock: _Clock = monotonic,
) -> bytes:
    length = parse_hex_length(
        _recv_exact(sock, 4, deadline=deadline, clock=clock), context=context
    )
    return _recv_exact(sock, length, deadline=deadline, clock=clock)


def _handshake(sock: socket.socket, deadline: float, clock: _Clock) -> None:
    _set_deadline_timeout(sock, deadline, clock)
    sock.sendall(encode_service(TRACK_DEVICES_PROTO_BINARY_SERVICE))
    status = _recv_exact(sock, 4, deadline=deadline, clock=clock)
    if status == b"OKAY":
        return
    if status == b"FAIL":
        detail_raw = _read_frame(sock, context="service error", deadline=deadline, clock=clock)
        try:
            detail = detail_raw.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise AdbProtocolError("ADB service error is not valid UTF-8") from exc
        raise AdbServiceError(
            TRACK_DEVICES_PROTO_BINARY_SERVICE,
            detail or "ADB server rejected track-devices",
        )
    raise AdbProtocolError(f"unexpected ADB service status: {status!r}")


class AospTrackDevicesStream:
    """One live AOSP track-devices smart-socket stream."""

    __slots__ = (
        "_socket",
        "_initial",
        "_lock",
        "_cancelled",
        "_closed",
        "_updates",
    )

    def __init__(self, sock: socket.socket, initial: Devices) -> None:
        if not isinstance(initial, Devices):
            raise TypeError("initial must be AOSP Devices")
        self._socket = sock
        self._initial = initial
        self._lock = Lock()
        self._cancelled = False
        self._closed = False
        self._updates = self._iterate_updates()

    @property
    def initial(self) -> Devices:
        return self._initial

    def updates(self) -> Iterator[Devices]:
        return self._updates

    def _is_closed(self) -> bool:
        with self._lock:
            return self._closed

    def _is_cancelled(self) -> bool:
        with self._lock:
            return self._cancelled

    def _iterate_updates(self) -> Iterator[Devices]:
        while not self._is_closed():
            try:
                devices = parse_devices(_read_frame(self._socket))
            except BaseException:
                if self._is_cancelled():
                    return
                raise
            yield devices

    @staticmethod
    def _shutdown(sock: socket.socket) -> None:
        try:
            sock.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass

    def close(self) -> None:
        """Interrupt the stream and close its physical socket.

        Cleanup orchestration is intentionally absent. A future pool cleaner may call this method;
        failures propagate and the retired pool entry remains available for retry policy outside
        this module.
        """

        with self._lock:
            self._cancelled = True
            if self._closed:
                return
            sock = self._socket
            self._shutdown(sock)
            sock.close()
            self._closed = True


class AospTrackDevicesStreamFactory:
    """Open fully initialized AOSP track-devices streams over smart sockets.

    Every socket is registered in the supplied resource scope immediately after creation. On a
    successful open, ownership is transferred to the registered ``AospTrackDevicesStream`` entry.
    """

    def __init__(
        self,
        startup_timeout_seconds: float = 5.0,
        *,
        _resolver: _Resolver = socket.getaddrinfo,
        _socket_factory: _SocketFactory = socket.socket,
        _clock: _Clock = monotonic,
    ) -> None:
        if not callable(_resolver) or not callable(_socket_factory) or not callable(_clock):
            raise TypeError("resolver, socket factory, and clock must be callable")
        self.startup_timeout_seconds = _normalize_startup_timeout(startup_timeout_seconds)
        self._resolver = DeadlineResolver(_resolver, _clock)
        self._socket_factory = _socket_factory
        self._clock = _clock

    def open(
        self,
        server_address: TcpAddress,
        cancellation: Event | None,
        resources: ResourceScope,
    ) -> AospTrackDevicesStream:
        """Connect, initialize, and register a track-devices stream."""

        if not isinstance(server_address, TcpAddress):
            raise TypeError("server_address must be TcpAddress")
        if cancellation is not None and not isinstance(cancellation, Event):
            raise TypeError("cancellation must be threading.Event or None")
        if not isinstance(resources, ResourceScope):
            raise TypeError("resources must be ResourceScope")

        _check_cancelled(cancellation)
        sock: socket.socket | None = None
        socket_entry: ResourceEntry | None = None
        try:
            sock, socket_entry, deadline = self._connect(
                server_address,
                cancellation,
                resources,
            )
            _check_cancelled(cancellation)
            _handshake(sock, deadline, self._clock)
            _check_cancelled(cancellation)
            initial = parse_devices(_read_frame(sock, deadline=deadline, clock=self._clock))
            _check_cancelled(cancellation)
            sock.settimeout(None)
            _check_cancelled(cancellation)

            stream = AospTrackDevicesStream(sock, initial)
            resources.register(stream)
            resources.discard(socket_entry)
            return stream
        except BaseException:
            if sock is not None and socket_entry is not None:
                if _close_socket(sock):
                    resources.discard(socket_entry)
                else:
                    resources.retire(socket_entry)
            raise

    def _connect(
        self,
        server_address: TcpAddress,
        cancellation: Event | None,
        resources: ResourceScope,
    ) -> tuple[socket.socket, ResourceEntry, float]:
        deadline = self._clock() + self.startup_timeout_seconds
        try:
            addresses = self._resolver.resolve(
                server_address.host,
                server_address.port,
                deadline=deadline,
                cancellation=cancellation,
            )
        except AddressResolutionCancelled as exc:
            raise AospTrackDevicesOpenCancelled from exc
        except OSError as exc:
            raise AdbServerConnectionError(
                f"failed to resolve ADB server address {server_address.host!r}: {exc}"
            ) from exc

        _check_cancelled(cancellation)
        last_error: OSError | None = None
        for family, socktype, proto, _, sockaddr in addresses:
            _check_cancelled(cancellation)
            candidate: socket.socket | None = None
            entry: ResourceEntry | None = None
            try:
                candidate = self._socket_factory(family, socktype, proto)
                entry = resources.register(candidate)
                _set_deadline_timeout(candidate, deadline, self._clock)
                candidate.connect(sockaddr)
                return candidate, entry, deadline
            except OSError as exc:
                if candidate is not None and entry is not None:
                    if _close_socket(candidate):
                        resources.discard(entry)
                    else:
                        resources.retire(entry)
                        raise
                last_error = exc
            except BaseException:
                if candidate is not None and entry is not None:
                    if _close_socket(candidate):
                        resources.discard(entry)
                    else:
                        resources.retire(entry)
                raise

        detail = str(last_error) if last_error is not None else "no address candidates"
        raise AdbServerConnectionError(f"failed to connect to ADB server: {detail}") from last_error


__all__ = [
    "AospTrackDevicesOpenCancelled",
    "AospTrackDevicesStream",
    "AospTrackDevicesStreamFactory",
]
