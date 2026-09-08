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
from adb.cleanup import CleanupHandoff, CleanupHandoffError, CleanupSink
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


def _cleanup_diagnostic(prefix: str, exc: BaseException) -> str:
    detail = str(exc).strip() or type(exc).__name__
    return f"{prefix}: {detail}"


def _close_after_failure(
    sock: socket.socket,
    *,
    context: str,
) -> CleanupHandoff | None:
    try:
        sock.close()
    except BaseException as exc:
        return CleanupHandoff(
            resource=sock,
            diagnostic=_cleanup_diagnostic(context, exc),
        )
    return None


class _WatchStartupCleanupHandoffRequired(RuntimeError):
    def __init__(
        self,
        primary_error: BaseException,
        cleanup_handoff: CleanupHandoff,
    ) -> None:
        self.primary_error = primary_error
        self.cleanup_handoff = cleanup_handoff
        super().__init__(str(primary_error).strip() or type(primary_error).__name__)


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


class _SmartSocketWatchHandle:
    """Backend-owned smart-socket physical handle and transport-list data source.

    Producers only receive a narrow stream view exposing ``initial``/``updates()``. ``cancel()``
    and ``close()`` remain backend-only physical lifecycle operations.
    """

    __slots__ = (
        "_socket",
        "_initial",
        "_lock",
        "_cancelled",
        "_closed",
        "_cleanup_handoff",
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
        self._cleanup_handoff: CleanupHandoff | None = None
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
                # Data-plane failure may opportunistically close the socket, but it cannot
                # transfer cleanup ownership. Any unresolved cleanup is retained atomically in
                # this handle until lifecycle release takes it.
                self._close_or_handoff(cancelled=False, retain_handoff=True)
                error = _watch_error(exc)
                if error is not None:
                    raise error from exc
                raise
            # A logical release may race after this read and before the yield. This stream is a
            # watch data-plane capability only; consumers coordinate any downstream relevance.
            yield transport_list

    def _close_or_handoff(
        self,
        *,
        cancelled: bool,
        retain_handoff: bool = False,
    ) -> CleanupHandoff | None:
        # Keep close/retention/transfer under one handle lock so a data-plane failure cannot
        # retain unresolved cleanup after a concurrent lifecycle release has already returned.
        with self._lock:
            if cancelled:
                self._cancelled = True

            retained = self._cleanup_handoff
            if retained is not None:
                if retain_handoff:
                    return None
                self._cleanup_handoff = None
                return retained

            if self._closed:
                return None
            self._closed = True
            sock = self._socket

            shutdown_error: BaseException | None = None
            try:
                self._shutdown(sock)
            except BaseException as exc:
                shutdown_error = exc
            try:
                sock.close()
            except BaseException as exc:
                diagnostic = _cleanup_diagnostic(
                    "ADB track-devices socket cleanup was not confirmed",
                    exc,
                )
                if shutdown_error is not None:
                    diagnostic += "; " + _cleanup_diagnostic(
                        "socket shutdown also failed",
                        shutdown_error,
                    )
                handoff = CleanupHandoff(
                    resource=sock,
                    diagnostic=diagnostic,
                )
                if retain_handoff:
                    self._cleanup_handoff = handoff
                    return None
                return handoff
            return None

    def cancel(self) -> CleanupHandoff | None:
        """Request non-blocking retirement or transfer unresolved socket cleanup."""

        return self._close_or_handoff(cancelled=True)

    def close(self) -> CleanupHandoff | None:
        return self._close_or_handoff(cancelled=True)


class SmartSocketAdbTransportListWatchBackend(AdbTransportListWatchBackendTemplate):
    """Generation-fenced transport-list watch authority over AOSP track-devices I/O.

    Lifecycle authority and resource ownership are linearized by the shared backend
    template. The adapter establishes a fully usable smart-socket handle, translates expected
    I/O failures, and provides non-blocking cancellation that interrupts a retired handle
    without making physical teardown part of the public release lifecycle.

    DNS resolution itself remains synchronous. Cancellation is observed before and after
    each blocking startup stage; matching ``release()`` still revokes authority immediately
    even when an in-flight system call takes until its configured deadline to return.
    """

    def __init__(
        self,
        generation_issuer: AdbTransportListWatchGenerationIssuer,
        *,
        cleanup_sink: CleanupSink,
        startup_timeout_seconds: float = 5.0,
        _resolver: Callable[..., list[tuple]] = socket.getaddrinfo,
        _socket_factory: Callable[..., socket.socket] = socket.socket,
        _clock: _Clock = monotonic,
    ) -> None:
        if not isinstance(generation_issuer, AdbTransportListWatchGenerationIssuer):
            raise TypeError("generation_issuer must be AdbTransportListWatchGenerationIssuer")
        if not callable(_resolver) or not callable(_socket_factory) or not callable(_clock):
            raise TypeError("resolver, socket factory, and clock must be callable")
        super().__init__(generation_issuer, cleanup_sink=cleanup_sink)
        self._startup_timeout_seconds = _normalize_timeout(startup_timeout_seconds)
        self._resolver = _resolver
        self._socket_factory = _socket_factory
        self._clock = _clock

    @staticmethod
    def _check_cancelled(cancellation: Event) -> None:
        if cancellation.is_set():
            raise AdbTransportListWatchBackendAcquireInterruptedError

    def _obtain_handle(
        self,
        endpoint: TcpAddress,
        cancellation: Event,
    ) -> _SmartSocketWatchHandle:
        timeout = self._startup_timeout_seconds
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

            return _SmartSocketWatchHandle(sock, initial)
        except _WatchStartupCleanupHandoffRequired as exc:
            failure = _watch_failure(exc.primary_error)
            if failure is not None:
                raise AdbTransportListWatchBackendAcquireError(
                    failure,
                    cleanup_handoff=exc.cleanup_handoff,
                ) from exc
            raise CleanupHandoffError(
                exc.primary_error,
                exc.cleanup_handoff,
            ) from exc
        except AdbTransportListWatchBackendAcquireInterruptedError as exc:
            cleanup_handoff = (
                None
                if sock is None
                else _close_after_failure(
                    sock,
                    context="ADB watch startup cancellation cleanup was not confirmed",
                )
            )
            if cleanup_handoff is None:
                raise
            raise AdbTransportListWatchBackendAcquireInterruptedError(
                cleanup_handoff
            ) from exc
        except BaseException as exc:
            cleanup_handoff = (
                None
                if sock is None
                else _close_after_failure(
                    sock,
                    context="ADB watch startup cleanup was not confirmed",
                )
            )
            failure = _watch_failure(exc)
            if failure is not None:
                raise AdbTransportListWatchBackendAcquireError(
                    failure,
                    cleanup_handoff=cleanup_handoff,
                ) from exc
            if cleanup_handoff is not None:
                raise CleanupHandoffError(
                    exc,
                    cleanup_handoff,
                ) from exc
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
                    cleanup_handoff = _close_after_failure(
                        candidate,
                        context="failed to close rejected ADB watch connection candidate",
                    )
                    if cleanup_handoff is not None:
                        raise _WatchStartupCleanupHandoffRequired(
                            exc,
                            cleanup_handoff,
                        ) from exc
                last_error = exc
            except BaseException as exc:
                if candidate is not None:
                    cleanup_handoff = _close_after_failure(
                        candidate,
                        context="failed to close ADB watch connection candidate after exception",
                    )
                    if cleanup_handoff is not None:
                        raise _WatchStartupCleanupHandoffRequired(
                            exc,
                            cleanup_handoff,
                        ) from exc
                raise

        detail = str(last_error) if last_error is not None else "no address candidates"
        raise AdbServerConnectionError(f"failed to connect to ADB server: {detail}") from last_error


__all__ = ["SmartSocketAdbTransportListWatchBackend"]
