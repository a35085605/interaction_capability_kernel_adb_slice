from __future__ import annotations

from collections.abc import Callable, Iterator
from math import isfinite
from numbers import Real
import socket
from threading import Lock
from time import monotonic

from _lifecycle_new.resource.driver import (
    PhysicalResources,
    RequirementAcquireFailed,
    RequirementAcquireResult,
    RequirementAcquireSucceeded,
)
from adb._resolution import DeadlineResolver
from adb.aosp.model.track_devices import Devices, parse_devices
from adb.aosp.protocol.smart_socket.framing import encode_service, parse_hex_length
from adb.aosp.protocol.smart_socket.services import TRACK_DEVICES_PROTO_BINARY_SERVICE
from adb.errors import (
    AdbProtocolError,
    AdbServerConnectionError,
    AdbServiceError,
    AdbTimeoutError,
)
from networking import TcpEndpoint


_Clock = Callable[[], float]
_Resolver = Callable[..., list[tuple]]
_SocketFactory = Callable[..., socket.socket]


def _normalize_startup_timeout(value: object) -> float:
    if isinstance(value, bool) or not isinstance(value, Real):
        raise TypeError("startup_timeout_seconds must be a real number")
    timeout = float(value)
    if not isfinite(timeout) or timeout <= 0:
        raise ValueError("startup_timeout_seconds must be finite and greater than zero")
    return timeout


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


class AospTrackDevicesSession:
    """Lifecycle-owned AOSP track-devices session.

    The session is the physical-resource ownership boundary. It privately owns the
    underlying OS socket, carries the initialized first ``Devices`` record, streams
    subsequent records, and synchronously closes the socket during lifecycle release.
    """

    __slots__ = (
        "_socket",
        "_initial",
        "_lock",
        "_cancelled",
        "_closed",
        "_updates",
    )

    def __init__(self, sock: socket.socket) -> None:
        self._socket = sock
        self._initial: Devices | None = None
        self._lock = Lock()
        self._cancelled = False
        self._closed = False
        self._updates: Iterator[Devices] | None = None

    def initialize(self, initial: Devices) -> None:
        """Publish the first record after the smart-socket handshake is complete."""

        if not isinstance(initial, Devices):
            raise TypeError("initial must be AOSP Devices")
        if self._initial is not None:
            raise RuntimeError("track-devices session is already initialized")
        self._initial = initial
        self._updates = self._iterate_updates()

    @property
    def initial(self) -> Devices:
        initial = self._initial
        if initial is None:
            raise RuntimeError("track-devices session is not initialized")
        return initial

    def updates(self) -> Iterator[Devices]:
        updates = self._updates
        if updates is None:
            raise RuntimeError("track-devices session is not initialized")
        return updates

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
        """Interrupt reads and synchronously close the privately owned OS socket."""

        with self._lock:
            self._cancelled = True
            if self._closed:
                return
            sock = self._socket
            self._shutdown(sock)
            sock.close()
            self._closed = True


class AospTrackDevicesSessionDriver:
    """Acquire initialized track-devices sessions and clean them up synchronously."""

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

    def acquire(
        self,
        server_endpoint: TcpEndpoint,
    ) -> RequirementAcquireResult[AospTrackDevicesSession]:
        """Create one initialized session and report retained cleanup debt on failure."""

        if not isinstance(server_endpoint, TcpEndpoint):
            raise TypeError("server_endpoint must be TcpEndpoint")

        deadline = self._clock() + self.startup_timeout_seconds
        try:
            addresses = self._resolver.resolve(
                server_endpoint.host,
                server_endpoint.port,
                deadline=deadline,
                cancellation=None,
            )
        except Exception as exc:
            if isinstance(exc, AdbTimeoutError):
                return RequirementAcquireFailed(exc, ())
            if isinstance(exc, OSError):
                error = AdbServerConnectionError(
                    f"failed to resolve ADB server address {server_endpoint.host!r}: {exc}"
                )
                return RequirementAcquireFailed(error, ())
            return RequirementAcquireFailed(exc, ())

        if not addresses:
            return RequirementAcquireFailed(
                AdbServerConnectionError("ADB server address resolution returned no candidates"),
                (),
            )

        last_error: Exception | None = None
        for address in addresses:
            if len(address) < 5:
                last_error = AdbServerConnectionError(
                    "ADB server address resolver returned a malformed candidate"
                )
                continue
            family, socktype, proto, _, sockaddr = address[:5]
            if not all(isinstance(value, int) for value in (family, socktype, proto)):
                last_error = AdbServerConnectionError(
                    "ADB server address resolver returned invalid socket metadata"
                )
                continue

            try:
                sock = self._socket_factory(family, socktype, proto)
            except OSError as exc:
                last_error = exc
                continue

            session = AospTrackDevicesSession(sock)
            try:
                _set_deadline_timeout(sock, deadline, self._clock)
                sock.connect(sockaddr)
            except OSError as exc:
                last_error = exc
                try:
                    session.close()
                except BaseException:
                    return RequirementAcquireFailed(exc, (session,))
                continue
            except Exception as exc:
                try:
                    session.close()
                except BaseException:
                    return RequirementAcquireFailed(exc, (session,))
                return RequirementAcquireFailed(exc, ())

            try:
                _handshake(sock, deadline, self._clock)
                initial = parse_devices(
                    _read_frame(sock, deadline=deadline, clock=self._clock)
                )
                sock.settimeout(None)
                session.initialize(initial)
            except Exception as exc:
                try:
                    session.close()
                except BaseException:
                    return RequirementAcquireFailed(exc, (session,))
                return RequirementAcquireFailed(exc, ())

            return RequirementAcquireSucceeded((session,))

        detail = str(last_error) if last_error is not None else "no address candidate succeeded"
        return RequirementAcquireFailed(
            AdbServerConnectionError(f"failed to connect to ADB server: {detail}"),
            (),
        )

    def cleanup(
        self,
        resources: PhysicalResources[AospTrackDevicesSession],
    ) -> None:
        """Close every retained session, tolerating retries after partial cleanup."""

        if not isinstance(resources, tuple):
            raise TypeError("resources must be a PhysicalResources tuple")

        first_error: BaseException | None = None
        for resource in reversed(resources):
            if not isinstance(resource, AospTrackDevicesSession):
                if first_error is None:
                    first_error = TypeError(
                        "resources must contain only AospTrackDevicesSession values"
                    )
                continue
            try:
                resource.close()
            except BaseException as exc:
                if first_error is None:
                    first_error = exc

        if first_error is not None:
            raise first_error


__all__ = [
    "AospTrackDevicesSession",
    "AospTrackDevicesSessionDriver",
]
