from __future__ import annotations

from collections.abc import Callable
import os
import socket
import subprocess
from threading import Event, Lock
from time import monotonic, sleep
from typing import Protocol

from adb._resolution import AddressResolutionCancelled, DeadlineResolver
from adb.adapters.subprocess.command import normalize_executable, normalize_timeout
from adb.errors import AdbError, AdbTimeoutError
from adb.aosp.io.smart_socket import AdbServiceClient
from networking import TcpAddress
from adb.server.endpoint import AdbServerEndpoint
from adb.server.generation import AdbServerGenerationIssuer
from adb.cleanup import CleanupHandoff
from adb.server.lifecycle.template import (
    AdbServerAcquireError,
    AdbServerAcquireInterruptedError,
    AdbServerLifecycleTemplate,
)
from adb.aosp.io.server_status import SmartSocketAdbServerStatusReader


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


class _AdbServerSubprocessCleanupRequired(_AdbServerSubprocessStartError):
    """Startup failed with resources whose local cleanup was not confirmed."""

    def __init__(
        self,
        primary_error: BaseException,
        cleanup_resources: tuple[object, ...],
        cleanup_endpoint: AdbServerEndpoint | None,
    ) -> None:
        if not isinstance(primary_error, BaseException):
            raise TypeError("primary_error must be BaseException")
        if not cleanup_resources or any(resource is None for resource in cleanup_resources):
            raise ValueError("cleanup_resources must contain non-None resources")
        if cleanup_endpoint is not None and not isinstance(cleanup_endpoint, TcpAddress):
            raise TypeError("cleanup_endpoint must be TcpAddress or None")
        self.primary_error = primary_error
        self.cleanup_resources = cleanup_resources
        self.cleanup_endpoint = cleanup_endpoint
        super().__init__(f"{primary_error}; subprocess startup cleanup remains pending")


def _merge_cleanup_resources(*resources: object | None) -> tuple[object, ...]:
    return tuple(resource for resource in resources if resource is not None)


def _close_socket_or_cleanup_resource(sock: socket.socket) -> object | None:
    try:
        sock.close()
    except BaseException:
        return sock
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

    def unresolved_cleanup_resource(self) -> subprocess.Popen[bytes]:
        """Return the underlying process after local cleanup could not be confirmed."""

        with self._lock:
            self._closed = True
            return self._process


class _AdbServerSubprocessFactory:
    """Create ready foreground ADB server processes through infrastructure seams.

    DNS waits are bounded and cancellable. Resource-producing factory calls must return in bounded
    time themselves; they are never detached while they could still bind a listener or spawn a child.
    Startup uses one deadline across resolution, launch, and readiness, with checks between stages.
    Injected status readers must also bound their I/O; cancellation is checked between probes.
    """

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
        self._resolver = DeadlineResolver(resolver, monotonic_clock)
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
                "a platform-specific server lifecycle implementation is required"
            )

        deadline = self._monotonic() + self.startup_timeout_seconds
        attachment, resolved_endpoint = self._launch(
            endpoint, deadline=deadline, cancellation=cancellation
        )
        try:
            if cancellation is not None and cancellation.is_set():
                raise _AdbServerSubprocessAcquireInterrupted
            self._wait_until_ready(
                resolved_endpoint,
                attachment._process,
                cancellation=cancellation,
                deadline=deadline,
            )
        except BaseException as startup_error:
            try:
                attachment.close()
            except BaseException as cleanup_error:
                cleanup_resource = attachment.unresolved_cleanup_resource()
                raise _AdbServerSubprocessCleanupRequired(
                    startup_error,
                    (cleanup_resource,),
                    resolved_endpoint,
                ) from cleanup_error
            raise
        return attachment, resolved_endpoint

    def _launch(
        self,
        endpoint: AdbServerEndpoint | None,
        *,
        deadline: float,
        cancellation: Event | None,
    ) -> tuple[_OwnedAdbServerProcess, AdbServerEndpoint]:
        reservation, resolved_endpoint = self._reserve_listener(
            endpoint, deadline=deadline, cancellation=cancellation
        )
        fd = reservation.fileno()
        try:
            self._check_startup(deadline, cancellation)
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
            cleanup_resource = _close_socket_or_cleanup_resource(reservation)
            if cleanup_resource is not None:
                raise _AdbServerSubprocessCleanupRequired(
                    primary,
                    (cleanup_resource,),
                    resolved_endpoint,
                ) from exc
            if primary is exc:
                raise
            raise primary from exc

        attachment = _OwnedAdbServerProcess(
            process,
            self.shutdown_timeout_seconds,
        )
        reservation_cleanup_resource = _close_socket_or_cleanup_resource(reservation)
        if reservation_cleanup_resource is None:
            return attachment, resolved_endpoint

        primary = _AdbServerSubprocessStartError(
            "ADB server child launched but parent listener reservation cleanup was not confirmed"
        )
        process_cleanup_resource: object | None = None
        try:
            attachment.close()
        except BaseException as cleanup_error:
            process_cleanup_resource = attachment.unresolved_cleanup_resource()
        cleanup_resources = _merge_cleanup_resources(
            reservation_cleanup_resource, process_cleanup_resource
        )
        if not cleanup_resources:
            raise RuntimeError("cleanup resource state is inconsistent")
        raise _AdbServerSubprocessCleanupRequired(
            primary,
            cleanup_resources,
            resolved_endpoint,
        )

    def _reserve_listener(
        self,
        endpoint: AdbServerEndpoint | None,
        *,
        deadline: float,
        cancellation: Event | None,
    ) -> tuple[socket.socket, AdbServerEndpoint]:
        host = endpoint.host if endpoint is not None else "127.0.0.1"
        port = endpoint.port if endpoint is not None else 0

        try:
            addresses = self._resolver.resolve(
                host, port, deadline=deadline, cancellation=cancellation
            )
        except AddressResolutionCancelled as exc:
            raise _AdbServerSubprocessAcquireInterrupted from exc
        except AdbTimeoutError as exc:
            raise _AdbServerSubprocessStartError(str(exc)) from exc
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
            self._check_startup(deadline, cancellation)
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
                cleanup_resource = _close_socket_or_cleanup_resource(listener)
                primary: BaseException = (
                    _AdbServerSubprocessStartError(
                        f"failed to prepare ADB server listener candidate: {exc}"
                    )
                    if isinstance(exc, OSError)
                    else exc
                )
                if cleanup_resource is not None:
                    raise _AdbServerSubprocessCleanupRequired(
                        primary,
                        (cleanup_resource,),
                        endpoint,
                    ) from exc
                if primary is exc:
                    raise
                failures.append(str(exc))

        detail = "; ".join(failures) or "no bind candidate succeeded"
        raise _AdbServerSubprocessStartError(
            f"failed to reserve ADB server listener: {detail}"
        )

    def _check_startup(self, deadline: float, cancellation: Event | None) -> None:
        if cancellation is not None and cancellation.is_set():
            raise _AdbServerSubprocessAcquireInterrupted
        if self._monotonic() >= deadline:
            raise _AdbServerSubprocessStartError("ADB server startup timed out")

    def _wait_until_ready(
        self,
        endpoint: AdbServerEndpoint,
        process: subprocess.Popen[bytes],
        *,
        cancellation: Event | None = None,
        deadline: float | None = None,
    ) -> None:
        if cancellation is not None and not isinstance(cancellation, Event):
            raise TypeError("cancellation must be threading.Event or None")
        if deadline is None:
            deadline = self._monotonic() + self.startup_timeout_seconds
        last_error: AdbError | None = None
        while True:
            self._check_startup(deadline, cancellation)

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
                self._check_startup(deadline, cancellation)
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


class SubprocessAdbServerLifecycle(AdbServerLifecycleTemplate[_OwnedAdbServerProcess]):
    """Provide ADB server access through an owned foreground subprocess."""

    def __init__(
        self,
        generation_issuer: AdbServerGenerationIssuer,
        *,
        cleanup_handoff: CleanupHandoff,
        executable: str = "adb",
        startup_timeout_seconds: float = 5.0,
        shutdown_timeout_seconds: float = 5.0,
        probe_interval_seconds: float = 0.05,
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
        super().__init__(generation_issuer, cleanup_handoff=cleanup_handoff)

    def _obtain_handle(
        self,
        endpoint_constraint: AdbServerEndpoint | None,
        cancellation: Event,
    ) -> tuple[_OwnedAdbServerProcess, AdbServerEndpoint]:
        try:
            return self._factory.create(endpoint_constraint, cancellation)
        except _AdbServerSubprocessCleanupRequired as exc:
            for resource in exc.cleanup_resources:
                self._schedule_unresolved_cleanup(resource, exc.cleanup_endpoint)
            if isinstance(exc.primary_error, _AdbServerSubprocessAcquireInterrupted):
                raise AdbServerAcquireInterruptedError() from exc
            if isinstance(exc.primary_error, _AdbServerSubprocessStartError):
                raise AdbServerAcquireError(
                    str(exc.primary_error).strip() or type(exc.primary_error).__name__
                ) from exc
            raise exc.primary_error from exc
        except _AdbServerSubprocessAcquireInterrupted as exc:
            raise AdbServerAcquireInterruptedError() from exc
        except _AdbServerSubprocessStartError as exc:
            raise AdbServerAcquireError(str(exc)) from exc

    def _attempt_local_cleanup(
        self,
        handle: _OwnedAdbServerProcess,
    ) -> object | None:
        try:
            handle.close()
        except BaseException:
            return handle.unresolved_cleanup_resource()
        return None


__all__ = ["SubprocessAdbServerLifecycle"]
