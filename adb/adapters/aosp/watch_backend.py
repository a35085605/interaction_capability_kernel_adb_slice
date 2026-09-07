from __future__ import annotations

from collections.abc import Callable, Iterator
from math import isfinite
from numbers import Real
import socket
from threading import Event, Lock
from time import monotonic

from adb.adapters.aosp.track_devices import to_transport_list
from adb.aosp.model.track_devices import parse_devices
from adb.aosp.protocol.smart_socket.framing import encode_service, parse_hex_length
from adb.aosp.protocol.smart_socket.services import TRACK_DEVICES_PROTO_BINARY_SERVICE
from adb.errors import (
    AdbProtocolError,
    AdbServerConnectionError,
    AdbServiceError,
    AdbTimeoutError,
)
from adb.transport_list.model import AdbTransportList
from adb.transport_list.watch.backend_template import (
    AdbTransportListWatchBackendAcquireError,
    AdbTransportListWatchBackendAcquireInterruptedError,
    AdbTransportListWatchBackendTemplate,
)
from adb.transport_list.watch.error import AdbTransportListWatchError
from adb.transport_list.watch.failure import (
    AdbTransportListWatchFailure,
    AdbTransportListWatchProtocolFailure,
    AdbTransportListWatchServerConnectionFailure,
    AdbTransportListWatchServiceFailure,
)
from adb.transport_list.watch.generation import AdbTransportListWatchGenerationIssuer
from adb.transport_list.watch.session import AdbTransportListWatchSession
from networking import TcpAddress


_Clock = Callable[[], float]


def _normalize_timeout(value: object) -> float:
    if isinstance(value, bool) or not isinstance(value, Real):
        raise TypeError("startup_timeout_seconds must be a real number")
    timeout = float(value)
    if not isfinite(timeout) or timeout <= 0:
        raise ValueError("startup_timeout_seconds must be finite and greater than zero")
    return timeout


def _watch_failure(exc: BaseException) -> AdbTransportListWatchFailure | None:
    diagnostic = str(exc).strip() or None
    if isinstance(exc, AdbProtocolError):
        return AdbTransportListWatchProtocolFailure(diagnostic)
    if isinstance(exc, AdbServiceError):
        return AdbTransportListWatchServiceFailure(diagnostic)
    if isinstance(exc, (AdbServerConnectionError, OSError)):
        return AdbTransportListWatchServerConnectionFailure(diagnostic)
    return None


def _watch_error(exc: BaseException) -> AdbTransportListWatchError | None:
    failure = _watch_failure(exc)
    return None if failure is None else AdbTransportListWatchError(failure)


def _close_after_failure(sock: socket.socket) -> None:
    try:
        sock.close()
    except BaseException:
        # The original failure remains primary, including programming errors.
        pass


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
            TRACK_DEVICES_PROTO_BINARY_SERVICE, detail or "ADB server rejected track-devices"
        )
    raise AdbProtocolError(f"unexpected ADB service status: {status!r}")


class _SmartSocketWatchSession:
    """Backend-owned smart-socket session borrowed by one producer.

    ``cancel()`` is the cross-thread retirement operation used after generation revocation.
    It interrupts blocking socket I/O and suppresses teardown errors because logical release
    is already authoritative. ``close()`` remains the idempotent final-cleanup operation.
    """

    __slots__ = (
        "_socket",
        "_initial",
        "_lock",
        "_cancelled",
        "_closed",
        "_updates",
    )

    def __init__(
        self,
        sock: socket.socket,
        initial: AdbTransportList,
    ) -> None:
        if not isinstance(initial, AdbTransportList):
            raise TypeError("initial must be AdbTransportList")
        self._socket = sock
        self._initial = initial
        self._lock = Lock()
        self._cancelled = False
        self._closed = False
        self._updates = self._iterate_updates()

    @property
    def initial(self) -> AdbTransportList:
        return self._initial

    def updates(self) -> Iterator[AdbTransportList]:
        return self._updates

    def _is_closed(self) -> bool:
        with self._lock:
            return self._closed

    def _is_cancelled(self) -> bool:
        with self._lock:
            return self._cancelled

    def _begin_close(self, *, cancelled: bool) -> socket.socket | None:
        with self._lock:
            if cancelled:
                self._cancelled = True
            if self._closed:
                return None
            self._closed = True
            return self._socket

    @staticmethod
    def _shutdown(sock: socket.socket) -> None:
        try:
            sock.shutdown(socket.SHUT_RDWR)
        except OSError:
            # Shutdown is only used to wake a blocking reader. A disconnected/already-closed
            # socket is already in an acceptable retirement state.
            pass

    def _iterate_updates(self) -> Iterator[AdbTransportList]:
        while not self._is_closed():
            try:
                transport_list = to_transport_list(parse_devices(_read_frame(self._socket)))
            except BaseException as exc:
                if self._is_cancelled():
                    return
                try:
                    self.close()
                except BaseException:
                    pass
                error = _watch_error(exc)
                if error is not None:
                    raise error from exc
                raise
            # A release may race after this read and before the yield. Generation fencing at the
            # coordinator/backend boundary remains authoritative for any resulting observation.
            yield transport_list

    def cancel(self) -> None:
        """Best-effort non-blocking retirement used by backend logical release."""

        sock = self._begin_close(cancelled=True)
        if sock is None:
            return
        self._shutdown(sock)
        try:
            sock.close()
        except OSError:
            # Cancellation is housekeeping after logical revocation. It must not turn release
            # into a different lifecycle outcome.
            pass

    def close(self) -> None:
        sock = self._begin_close(cancelled=True)
        if sock is None:
            return
        self._shutdown(sock)
        try:
            sock.close()
        except OSError as exc:
            raise AdbTransportListWatchError(
                AdbTransportListWatchServerConnectionFailure(
                    f"failed to close ADB track-devices socket: {exc}"
                )
            ) from exc


class SmartSocketAdbTransportListWatchBackend(AdbTransportListWatchBackendTemplate):
    """Generation-fenced transport-list watch authority over AOSP track-devices I/O.

    Lifecycle authority and resource ownership are linearized by the shared backend
    template. The adapter establishes a fully usable smart-socket session, translates expected
    I/O failures, and provides non-blocking cancellation that interrupts a retired session
    without making physical teardown part of the public release lifecycle.

    DNS resolution itself remains synchronous. Cancellation is observed before and after
    each blocking startup stage; matching ``release()`` still revokes authority immediately
    even when an in-flight system call takes until its configured deadline to return.
    """

    def __init__(
        self,
        generation_issuer: AdbTransportListWatchGenerationIssuer,
        *,
        _resolver: Callable[..., list[tuple]] = socket.getaddrinfo,
        _socket_factory: Callable[..., socket.socket] = socket.socket,
        _clock: _Clock = monotonic,
    ) -> None:
        if not isinstance(generation_issuer, AdbTransportListWatchGenerationIssuer):
            raise TypeError("generation_issuer must be AdbTransportListWatchGenerationIssuer")
        if not callable(_resolver) or not callable(_socket_factory) or not callable(_clock):
            raise TypeError("resolver, socket factory, and clock must be callable")
        super().__init__(generation_issuer)
        self._resolver = _resolver
        self._socket_factory = _socket_factory
        self._clock = _clock

    @staticmethod
    def _check_cancelled(cancellation: Event) -> None:
        if cancellation.is_set():
            raise AdbTransportListWatchBackendAcquireInterruptedError

    def _obtain_session(
        self,
        endpoint: TcpAddress,
        startup_timeout_seconds: float,
        cancellation: Event,
    ) -> AdbTransportListWatchSession:
        timeout = _normalize_timeout(startup_timeout_seconds)
        self._check_cancelled(cancellation)

        sock: socket.socket | None = None
        try:
            sock, deadline = self._connect(endpoint, timeout)
            self._check_cancelled(cancellation)
            _handshake(sock, deadline, self._clock)
            self._check_cancelled(cancellation)
            initial = to_transport_list(
                parse_devices(_read_frame(sock, deadline=deadline, clock=self._clock))
            )
            self._check_cancelled(cancellation)
            sock.settimeout(None)
            self._check_cancelled(cancellation)

            return _SmartSocketWatchSession(sock, initial)
        except AdbTransportListWatchBackendAcquireInterruptedError:
            if sock is not None:
                _close_after_failure(sock)
            raise
        except BaseException as exc:
            if sock is not None:
                _close_after_failure(sock)
            failure = _watch_failure(exc)
            if failure is not None:
                raise AdbTransportListWatchBackendAcquireError(failure) from exc
            raise

    def _connect(self, endpoint: TcpAddress, timeout: float) -> tuple[socket.socket, float]:
        try:
            addresses = self._resolver(endpoint.host, endpoint.port, type=socket.SOCK_STREAM)
        except OSError as exc:
            raise AdbServerConnectionError(
                f"failed to resolve ADB server address {endpoint.host!r}: {exc}"
            ) from exc

        # Synchronous DNS resolution above is not bounded by the socket timeout. Connect
        # candidates, handshake, and the first complete frame share one deadline.
        deadline = self._clock() + timeout
        last_error: OSError | None = None
        for family, socktype, proto, _, sockaddr in addresses:
            candidate: socket.socket | None = None
            try:
                candidate = self._socket_factory(family, socktype, proto)
                _set_deadline_timeout(candidate, deadline, self._clock)
                candidate.connect(sockaddr)
                return candidate, deadline
            except OSError as exc:
                if candidate is not None:
                    _close_after_failure(candidate)
                last_error = exc
            except BaseException:
                if candidate is not None:
                    _close_after_failure(candidate)
                raise

        detail = str(last_error) if last_error is not None else "no address candidates"
        raise AdbServerConnectionError(f"failed to connect to ADB server: {detail}") from last_error


__all__ = ["SmartSocketAdbTransportListWatchBackend"]
