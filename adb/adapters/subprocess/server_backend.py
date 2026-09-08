from __future__ import annotations

from collections.abc import Callable
import os
import socket
import subprocess
from threading import Event, Lock
from time import monotonic, sleep
from typing import Protocol

from adb.adapters.subprocess.command import normalize_executable, normalize_timeout
from adb.errors import AdbError
from adb.aosp.io.smart_socket import AdbServiceClient
from networking import TcpAddress
from adb.server.endpoint import AdbServerEndpoint
from adb.server.generation import AdbServerGenerationIssuer
from adb.server.lifecycle.backend import (
    AdbServerBackendCleanupHandoff,
    AdbServerBackendCleanupHandoffError,
)
from adb.server.lifecycle.backend_template import (
    AdbServerBackendAcquireError,
    AdbServerBackendAcquireInterruptedError,
    AdbServerBackendTemplate,
)
from adb.aosp.io.server_status import SmartSocketAdbServerStatusReader
from eventing import EventPublisher


_MonotonicClock = Callable[[], float]
_Sleeper = Callable[[float], None]
_PopenFactory = Callable[..., subprocess.Popen[bytes]]
_Resolver = Callable[..., list[tuple[object, ...]]]
_SocketFactory = Callable[[int, int, int], socket.socket]


class _ServerStatusReader(Protocol):
    def read(self, endpoint: AdbServerEndpoint) -> object: ...


class _AdbServerSubprocessStartError(RuntimeError):
    """Infrastructure failure while creating a foreground ADB server child."""


class _AdbServerSubprocessAcquireInterrupted(RuntimeError):
    """Startup was interrupted after its server authority was released."""


class _AdbServerSubprocessTerminationUnconfirmed(RuntimeError):
    """Failure to confirm termination of an owned child process."""


class _AdbServerSubprocessCleanupHandoffRequired(_AdbServerSubprocessStartError):
    """Startup failed after unresolved physical cleanup ownership was transferred."""

    def __init__(
        self,
        primary_error: BaseException,
        cleanup_handoff: AdbServerBackendCleanupHandoff,
    ) -> None:
        if not isinstance(primary_error, BaseException):
            raise TypeError("primary_error must be BaseException")
        if not isinstance(cleanup_handoff, AdbServerBackendCleanupHandoff):
            raise TypeError("cleanup_handoff must be AdbServerBackendCleanupHandoff")
        self.primary_error = primary_error
        self.cleanup_handoff = cleanup_handoff
        super().__init__(
            f"{primary_error}; subprocess startup cleanup requires ownership handoff"
        )


def _cleanup_diagnostic(prefix: str, exc: BaseException) -> str:
    detail = str(exc).strip() or type(exc).__name__
    return f"{prefix}: {detail}"


def _merge_cleanup_handoffs(
    *handoffs: AdbServerBackendCleanupHandoff | None,
) -> AdbServerBackendCleanupHandoff | None:
    present = tuple(handoff for handoff in handoffs if handoff is not None)
    if not present:
        return None
    if len(present) == 1:
        return present[0]
    return AdbServerBackendCleanupHandoff(
        handle=tuple(handoff.handle for handoff in present),
        diagnostic="; ".join(handoff.diagnostic for handoff in present),
    )


def _close_socket_or_handoff(
    sock: socket.socket,
    *,
    context: str,
) -> AdbServerBackendCleanupHandoff | None:
    try:
        sock.close()
    except BaseException as exc:
        return AdbServerBackendCleanupHandoff(
            handle=sock,
            diagnostic=_cleanup_diagnostic(context, exc),
        )
    return None


def _normalize_probe_interval(value: object) -> float:
    normalized = normalize_timeout(value)
    if normalized > 1.0:
        raise ValueError("ADB server startup probe interval must be at most one second")
    return normalized


class _OwnedAdbServerProcess:
    """One foreground ADB server process whose lifetime is owned by this adapter."""

    def __init__(
        self,
        process: subprocess.Popen[bytes],
        shutdown_timeout_seconds: float,
    ) -> None:
        self._process = process
        self._shutdown_timeout_seconds = shutdown_timeout_seconds
        self._lock = Lock()
        self._closed = False

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            if self._process.poll() is not None:
                self._closed = True
                return

            try:
                self._process.terminate()
            except OSError as exc:
                if self._process.poll() is None:
                    raise _AdbServerSubprocessTerminationUnconfirmed(
                        f"failed to terminate owned ADB server child process: {exc}"
                    ) from exc

            try:
                self._process.wait(timeout=self._shutdown_timeout_seconds)
            except subprocess.TimeoutExpired:
                try:
                    self._process.kill()
                except OSError as exc:
                    if self._process.poll() is None:
                        raise _AdbServerSubprocessTerminationUnconfirmed(
                            f"failed to kill owned ADB server child process: {exc}"
                        ) from exc
                try:
                    self._process.wait(timeout=self._shutdown_timeout_seconds)
                except subprocess.TimeoutExpired as exc:
                    raise _AdbServerSubprocessTerminationUnconfirmed(
                        "ADB server child process did not terminate after kill"
                    ) from exc

            if self._process.poll() is None:
                raise _AdbServerSubprocessTerminationUnconfirmed(
                    "ADB server child-process termination was not confirmed"
                )
            self._closed = True

    def relinquish_cleanup_handle(self) -> subprocess.Popen[bytes]:
        """Transfer unresolved child-process cleanup ownership out of this adapter handle."""

        with self._lock:
            self._closed = True
            return self._process


class _AdbServerSubprocessFactory:
    """Create ready foreground ADB server processes through infrastructure seams."""

    def __init__(
        self,
        *,
        executable: str = "adb",
        startup_timeout_seconds: float = 5.0,
        shutdown_timeout_seconds: float = 5.0,
        probe_interval_seconds: float = 0.05,
        popen_factory: _PopenFactory = subprocess.Popen,
        resolver: _Resolver = socket.getaddrinfo,
        socket_factory: _SocketFactory = socket.socket,
        monotonic_clock: _MonotonicClock = monotonic,
        sleeper: _Sleeper = sleep,
        status_reader: _ServerStatusReader | None = None,
        socket_activation_supported: bool = os.name != "nt",
    ) -> None:
        if not isinstance(socket_activation_supported, bool):
            raise TypeError("socket_activation_supported must be a bool")

        self.executable = normalize_executable(executable)
        self.startup_timeout_seconds = normalize_timeout(startup_timeout_seconds)
        self.shutdown_timeout_seconds = normalize_timeout(shutdown_timeout_seconds)
        self.probe_interval_seconds = _normalize_probe_interval(probe_interval_seconds)
        self._popen_factory = popen_factory
        self._resolver = resolver
        self._socket_factory = socket_factory
        self._monotonic = monotonic_clock
        self._sleep = sleeper
        self._socket_activation_supported = socket_activation_supported

        if status_reader is None:
            read_timeout = min(0.25, self.startup_timeout_seconds)
            status_reader = SmartSocketAdbServerStatusReader(
                _client_factory=lambda candidate: AdbServiceClient(
                    candidate.host,
                    candidate.port,
                    timeout_seconds=read_timeout,
                )
            )
        if not callable(getattr(status_reader, "read", None)):
            raise TypeError("status_reader must provide read()")
        self._status_reader = status_reader

    def create(
        self,
        endpoint: AdbServerEndpoint | None,
        cancellation: Event | None = None,
    ) -> tuple[_OwnedAdbServerProcess, AdbServerEndpoint]:
        if cancellation is not None and not isinstance(cancellation, Event):
            raise TypeError("cancellation must be threading.Event or None")
        if cancellation is not None and cancellation.is_set():
            raise _AdbServerSubprocessAcquireInterrupted
        if not self._socket_activation_supported:
            raise _AdbServerSubprocessStartError(
                "ADB acceptfd socket activation is unavailable on this platform; "
                "a platform-specific server backend is required"
            )

        attachment, resolved_endpoint = self._launch(endpoint)
        try:
            if cancellation is not None and cancellation.is_set():
                raise _AdbServerSubprocessAcquireInterrupted
            self._wait_until_ready(
                resolved_endpoint,
                attachment._process,
                cancellation=cancellation,
            )
        except BaseException as startup_error:
            try:
                attachment.close()
            except BaseException as cleanup_error:
                handoff = AdbServerBackendCleanupHandoff(
                    handle=attachment.relinquish_cleanup_handle(),
                    diagnostic=_cleanup_diagnostic(
                        "ADB server child startup cleanup was not confirmed",
                        cleanup_error,
                    ),
                )
                raise _AdbServerSubprocessCleanupHandoffRequired(
                    startup_error,
                    handoff,
                ) from cleanup_error
            raise
        return attachment, resolved_endpoint

    def _launch(
        self,
        endpoint: AdbServerEndpoint | None,
    ) -> tuple[_OwnedAdbServerProcess, AdbServerEndpoint]:
        reservation, resolved_endpoint = self._reserve_listener(endpoint)
        fd = reservation.fileno()
        try:
            process = self._popen_factory(
                [
                    self.executable,
                    "server",
                    "nodaemon",
                    "-L",
                    f"acceptfd:{fd}",
                ],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                close_fds=True,
                pass_fds=(fd,),
            )
        except BaseException as exc:
            primary: BaseException = (
                _AdbServerSubprocessStartError(
                    f"failed to launch ADB server child process: {exc}"
                )
                if isinstance(exc, OSError)
                else exc
            )
            handoff = _close_socket_or_handoff(
                reservation,
                context="failed to close ADB server listener reservation after launch failure",
            )
            if handoff is not None:
                raise _AdbServerSubprocessCleanupHandoffRequired(
                    primary,
                    handoff,
                ) from exc
            if primary is exc:
                raise
            raise primary from exc

        attachment = _OwnedAdbServerProcess(
            process,
            self.shutdown_timeout_seconds,
        )
        reservation_handoff = _close_socket_or_handoff(
            reservation,
            context="failed to close parent ADB server listener reservation after child launch",
        )
        if reservation_handoff is None:
            return attachment, resolved_endpoint

        primary = _AdbServerSubprocessStartError(
            "ADB server child launched but parent listener reservation cleanup was not confirmed"
        )
        process_handoff: AdbServerBackendCleanupHandoff | None = None
        try:
            attachment.close()
        except BaseException as cleanup_error:
            process_handoff = AdbServerBackendCleanupHandoff(
                handle=attachment.relinquish_cleanup_handle(),
                diagnostic=_cleanup_diagnostic(
                    "ADB server child cleanup after listener-reservation failure was not confirmed",
                    cleanup_error,
                ),
            )
        combined = _merge_cleanup_handoffs(reservation_handoff, process_handoff)
        if combined is None:
            raise RuntimeError("cleanup handoff state is inconsistent")
        raise _AdbServerSubprocessCleanupHandoffRequired(primary, combined)

    def _reserve_listener(
        self,
        endpoint: AdbServerEndpoint | None,
    ) -> tuple[socket.socket, AdbServerEndpoint]:
        host = endpoint.host if endpoint is not None else "127.0.0.1"
        port = endpoint.port if endpoint is not None else 0

        try:
            addresses = self._resolver(host, port, type=socket.SOCK_STREAM)
        except OSError as exc:
            raise _AdbServerSubprocessStartError(
                f"failed to resolve ADB server bind address: {exc}"
            ) from exc
        if not addresses:
            raise _AdbServerSubprocessStartError(
                "ADB server bind address resolution returned no candidates"
            )

        failures: list[str] = []
        for address in addresses:
            if len(address) < 5:
                failures.append("resolver returned malformed address")
                continue
            family, socktype, proto, _, sockaddr = address[:5]
            if not all(isinstance(value, int) for value in (family, socktype, proto)):
                failures.append("resolver returned invalid socket metadata")
                continue
            try:
                listener = self._socket_factory(family, socktype, proto)
            except OSError as exc:
                failures.append(str(exc))
                continue
            try:
                listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                listener.bind(sockaddr)
                listener.listen(socket.SOMAXCONN)
                bound = listener.getsockname()
                resolved = TcpAddress(str(bound[0]), int(bound[1]))
                return listener, resolved
            except BaseException as exc:
                handoff = _close_socket_or_handoff(
                    listener,
                    context="failed to close rejected ADB server listener candidate",
                )
                primary: BaseException = (
                    _AdbServerSubprocessStartError(
                        f"failed to prepare ADB server listener candidate: {exc}"
                    )
                    if isinstance(exc, OSError)
                    else exc
                )
                if handoff is not None:
                    raise _AdbServerSubprocessCleanupHandoffRequired(
                        primary,
                        handoff,
                    ) from exc
                if primary is exc:
                    raise
                failures.append(str(exc))

        detail = "; ".join(failures) or "no bind candidate succeeded"
        raise _AdbServerSubprocessStartError(
            f"failed to reserve ADB server listener: {detail}"
        )

    def _wait_until_ready(
        self,
        endpoint: AdbServerEndpoint,
        process: subprocess.Popen[bytes],
        *,
        cancellation: Event | None = None,
    ) -> None:
        if cancellation is not None and not isinstance(cancellation, Event):
            raise TypeError("cancellation must be threading.Event or None")
        deadline = self._monotonic() + self.startup_timeout_seconds
        last_error: AdbError | None = None
        while True:
            if cancellation is not None and cancellation.is_set():
                raise _AdbServerSubprocessAcquireInterrupted

            return_code = process.poll()
            if return_code is not None:
                raise _AdbServerSubprocessStartError(
                    f"ADB server child process exited during startup with code {return_code}"
                )

            try:
                self._status_reader.read(endpoint)
            except AdbError as exc:
                last_error = exc
            else:
                if cancellation is not None and cancellation.is_set():
                    raise _AdbServerSubprocessAcquireInterrupted
                if process.poll() is not None:
                    raise _AdbServerSubprocessStartError(
                        "ADB server child process exited while startup readiness was being verified"
                    )
                return

            remaining = deadline - self._monotonic()
            if remaining <= 0.0:
                suffix = f": {last_error}" if last_error is not None else ""
                raise _AdbServerSubprocessStartError(
                    f"timed out waiting for created ADB server readiness{suffix}"
                )
            delay = min(self.probe_interval_seconds, remaining)
            if cancellation is None:
                self._sleep(delay)
            elif cancellation.wait(delay):
                raise _AdbServerSubprocessAcquireInterrupted


class SubprocessAdbServerBackend(AdbServerBackendTemplate[_OwnedAdbServerProcess]):
    """Provide ADB server access through an owned foreground subprocess."""

    def __init__(
        self,
        generation_issuer: AdbServerGenerationIssuer,
        *,
        executable: str = "adb",
        startup_timeout_seconds: float = 5.0,
        shutdown_timeout_seconds: float = 5.0,
        probe_interval_seconds: float = 0.05,
        publisher: EventPublisher | None = None,
        _factory: _AdbServerSubprocessFactory | None = None,
    ) -> None:
        if _factory is None:
            _factory = _AdbServerSubprocessFactory(
                executable=executable,
                startup_timeout_seconds=startup_timeout_seconds,
                shutdown_timeout_seconds=shutdown_timeout_seconds,
                probe_interval_seconds=probe_interval_seconds,
            )
        if not callable(getattr(_factory, "create", None)):
            raise TypeError("_factory must provide create()")

        self._factory = _factory
        super().__init__(generation_issuer, publisher=publisher)

    def _obtain_handle(
        self,
        endpoint_constraint: AdbServerEndpoint | None,
        cancellation: Event,
    ) -> tuple[_OwnedAdbServerProcess, AdbServerEndpoint]:
        try:
            return self._factory.create(endpoint_constraint, cancellation)
        except _AdbServerSubprocessCleanupHandoffRequired as exc:
            if isinstance(exc.primary_error, _AdbServerSubprocessAcquireInterrupted):
                raise AdbServerBackendAcquireInterruptedError(
                    exc.cleanup_handoff
                ) from exc
            if isinstance(exc.primary_error, _AdbServerSubprocessStartError):
                raise AdbServerBackendAcquireError(
                    str(exc.primary_error).strip() or type(exc.primary_error).__name__,
                    cleanup_handoff=exc.cleanup_handoff,
                ) from exc
            raise AdbServerBackendCleanupHandoffError(
                exc.primary_error,
                exc.cleanup_handoff,
            ) from exc
        except _AdbServerSubprocessAcquireInterrupted as exc:
            raise AdbServerBackendAcquireInterruptedError() from exc
        except _AdbServerSubprocessStartError as exc:
            raise AdbServerBackendAcquireError(str(exc)) from exc

    def _release_handle(
        self,
        handle: _OwnedAdbServerProcess,
    ) -> AdbServerBackendCleanupHandoff | None:
        try:
            handle.close()
        except BaseException as exc:
            return AdbServerBackendCleanupHandoff(
                handle=handle.relinquish_cleanup_handle(),
                diagnostic=_cleanup_diagnostic(
                    "ADB server child-process cleanup was not confirmed",
                    exc,
                ),
            )
        return None


__all__ = ["SubprocessAdbServerBackend"]
