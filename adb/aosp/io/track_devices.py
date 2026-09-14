from __future__ import annotations

from collections.abc import Callable, Iterator
from math import isfinite
from numbers import Real
import socket
from threading import Lock
from time import monotonic

from lifecycle.resource.cleanup import cleanup_reverse
from lifecycle.resource.driver import (
    PhysicalResources,
    RequirementAcquireFailed,
    RequirementAcquireInterrupted,
    RequirementAcquireResult,
    RequirementAcquireSucceeded,
)
from adb._deadline import Deadline
from adb._resolution import DeadlineResolver
from adb.aosp.io._smart_socket_protocol import (
    read_length_prefixed,
    read_service_response,
    recv_exact,
    send_service_request,
)
from adb.aosp.io.smart_socket import AdbServiceClient
from adb.aosp.model.track_devices import Devices, parse_devices
from adb.aosp.protocol.smart_socket.services import TRACK_DEVICES_PROTO_BINARY_SERVICE
from adb.errors import AdbServerConnectionError, AdbTimeoutError
from networking import TcpEndpoint


_Clock = Callable[[], float]
_Resolver = Callable[..., list[tuple]]
_SocketFactory = Callable[..., socket.socket]
_ClientFactory = Callable[[TcpEndpoint], AdbServiceClient]


def _normalize_startup_timeout(value: object) -> float:
    if isinstance(value, bool) or not isinstance(value, Real):
        raise TypeError("startup_timeout_seconds must be a real number")
    timeout = float(value)
    if not isfinite(timeout) or timeout <= 0:
        raise ValueError("startup_timeout_seconds must be finite and greater than zero")
    return timeout


def _coerce_deadline(deadline: Deadline | float, clock: _Clock) -> Deadline:
    if isinstance(deadline, Deadline):
        return deadline
    return Deadline.at(deadline, clock)


def _set_deadline_timeout(
    sock: socket.socket,
    deadline: Deadline | float,
    clock: _Clock = monotonic,
) -> None:
    deadline = _coerce_deadline(deadline, clock)
    remaining = deadline.remaining()
    if remaining <= 0:
        raise AdbTimeoutError("ADB track-devices startup timed out")
    sock.settimeout(remaining)


def _recv_exact(
    sock: socket.socket,
    size: int,
    *,
    deadline: Deadline | float | None = None,
    clock: _Clock = monotonic,
) -> bytes:
    resolved_deadline = None if deadline is None else _coerce_deadline(deadline, clock)

    def receive(remaining: int) -> bytes:
        if resolved_deadline is not None:
            _set_deadline_timeout(sock, resolved_deadline)
        return sock.recv(remaining)

    return recv_exact(
        receive,
        size,
        eof_message="unexpected EOF from ADB track-devices stream",
    )


def _read_frame(
    sock: socket.socket,
    *,
    context: str = "track-devices record",
    deadline: Deadline | float | None = None,
    clock: _Clock = monotonic,
) -> bytes:
    return read_length_prefixed(
        lambda size: _recv_exact(sock, size, deadline=deadline, clock=clock),
        context=context,
    )


def _handshake(
    sock: socket.socket,
    deadline: Deadline | float,
    clock: _Clock = monotonic,
) -> None:
    resolved_deadline = _coerce_deadline(deadline, clock)
    _set_deadline_timeout(sock, resolved_deadline)
    send_service_request(sock.sendall, TRACK_DEVICES_PROTO_BINARY_SERVICE)
    read_service_response(
        TRACK_DEVICES_PROTO_BINARY_SERVICE,
        lambda size: _recv_exact(sock, size, deadline=resolved_deadline),
        rejection_detail="ADB server rejected track-devices",
    )


def _failed_session_acquire(
    session: "AospTrackDevicesSession",
    error: BaseException,
) -> (
    RequirementAcquireFailed[AospTrackDevicesSession]
    | RequirementAcquireInterrupted[AospTrackDevicesSession]
):
    retained: tuple[AospTrackDevicesSession, ...] = ()
    try:
        session.close()
    except BaseException as close_error:
        retained = (session,)
        if not isinstance(close_error, Exception):
            return RequirementAcquireInterrupted(close_error, retained)

    if not isinstance(error, Exception):
        return RequirementAcquireInterrupted(error, retained)
    return RequirementAcquireFailed(error, retained)


def _default_client_factory(server_endpoint: TcpEndpoint) -> AdbServiceClient:
    return AdbServiceClient(server_endpoint.host, server_endpoint.port)


class SmartSocketAospTrackDevicesReader:
    """Read the first raw AOSP track-devices record for one server endpoint."""

    def __init__(self, *, _client_factory: _ClientFactory = _default_client_factory) -> None:
        if not callable(_client_factory):
            raise TypeError("_client_factory must be callable")
        self._client_factory = _client_factory

    def read(self, server_endpoint: TcpEndpoint) -> Devices:
        if not isinstance(server_endpoint, TcpEndpoint):
            raise TypeError("server_endpoint must be TcpEndpoint")
        payload = self._client_factory(server_endpoint).first_stream_frame(
            TRACK_DEVICES_PROTO_BINARY_SERVICE
        )
        return parse_devices(payload)


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

        deadline = Deadline.after(self.startup_timeout_seconds, self._clock)
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
                _set_deadline_timeout(sock, deadline)
                sock.connect(sockaddr)
            except OSError as exc:
                outcome = _failed_session_acquire(session, exc)
                if outcome.resources:
                    return outcome
                last_error = exc
                continue
            except BaseException as exc:
                return _failed_session_acquire(session, exc)

            try:
                _handshake(sock, deadline)
                initial = parse_devices(_read_frame(sock, deadline=deadline))
                sock.settimeout(None)
                session.initialize(initial)
            except BaseException as exc:
                return _failed_session_acquire(session, exc)

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

        cleanup_reverse(resources, self._close_session)

    @staticmethod
    def _close_session(resource: AospTrackDevicesSession) -> None:
        if not isinstance(resource, AospTrackDevicesSession):
            raise TypeError("resources must contain only AospTrackDevicesSession values")
        resource.close()


__all__ = [
    "AospTrackDevicesSession",
    "AospTrackDevicesSessionDriver",
    "SmartSocketAospTrackDevicesReader",
]
