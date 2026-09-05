from __future__ import annotations

from collections.abc import Callable
import os
import socket
import subprocess
from threading import Lock
from time import monotonic, sleep
from typing import Protocol

from adb.adapters.subprocess.command import normalize_executable, normalize_timeout
from adb.errors import AdbError
from adb.aosp.io.smart_socket import AdbServiceClient
from networking import TcpAddress
from adb.server.endpoint import AdbServerEndpoint
from adb.server.lifecycle.backend import (
    AdbServerBackendAcquireError,
    AdbServerBackendTemplate,
    AdbServerBackendReleaseCleanupUnconfirmed,
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


class _AdbServerSubprocessTerminationUnconfirmed(RuntimeError):
    """Failure to confirm termination of an owned child process."""


class _AdbServerSubprocessStartupCleanupUnconfirmed(_AdbServerSubprocessStartError):
    """Startup-cleanup failure with unconfirmed owned-child termination."""

    def __init__(
        self,
        termination_error: _AdbServerSubprocessTerminationUnconfirmed,
    ) -> None:
        self.termination_error = termination_error
        super().__init__(
            "ADB server child startup failed and child-process cleanup could not be confirmed"
        )


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
    ) -> tuple[_OwnedAdbServerProcess, AdbServerEndpoint]:
        if not self._socket_activation_supported:
            raise _AdbServerSubprocessStartError(
                "ADB acceptfd socket activation is unavailable on this platform; "
                "a platform-specific server backend is required"
            )

        attachment, resolved_endpoint = self._launch(endpoint)
        try:
            self._wait_until_ready(resolved_endpoint, attachment._process)
        except BaseException:
            try:
                attachment.close()
            except _AdbServerSubprocessTerminationUnconfirmed as termination_error:
                raise _AdbServerSubprocessStartupCleanupUnconfirmed(
                    termination_error
                ) from termination_error
            raise
        return attachment, resolved_endpoint

    def _launch(
        self,
        endpoint: AdbServerEndpoint | None,
    ) -> tuple[_OwnedAdbServerProcess, AdbServerEndpoint]:
        reservation, resolved_endpoint = self._reserve_listener(endpoint)
        try:
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
            except OSError as exc:
                raise _AdbServerSubprocessStartError(
                    f"failed to launch ADB server child process: {exc}"
                ) from exc
            return (
                _OwnedAdbServerProcess(
                    process,
                    self.shutdown_timeout_seconds,
                ),
                resolved_endpoint,
            )
        finally:
            reservation.close()

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
            except OSError as exc:
                failures.append(str(exc))
                try:
                    listener.close()
                except OSError:
                    pass

        detail = "; ".join(failures) or "no bind candidate succeeded"
        raise _AdbServerSubprocessStartError(
            f"failed to reserve ADB server listener: {detail}"
        )

    def _wait_until_ready(
        self,
        endpoint: AdbServerEndpoint,
        process: subprocess.Popen[bytes],
    ) -> None:
        deadline = self._monotonic() + self.startup_timeout_seconds
        last_error: AdbError | None = None
        while True:
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
            self._sleep(min(self.probe_interval_seconds, remaining))


class SubprocessAdbServerBackend(AdbServerBackendTemplate[_OwnedAdbServerProcess]):
    """Provide ADB server access through an owned foreground subprocess."""

    def __init__(
        self,
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
        super().__init__(publisher=publisher)

    def _obtain_handle(
        self,
        endpoint_constraint: AdbServerEndpoint | None,
    ) -> tuple[_OwnedAdbServerProcess, AdbServerEndpoint]:
        try:
            return self._factory.create(endpoint_constraint)
        except _AdbServerSubprocessStartupCleanupUnconfirmed as exc:
            raise AdbServerBackendAcquireError(
                "ADB subprocess backend acquire failed and child-process cleanup "
                "could not be completed"
            ) from exc
        except _AdbServerSubprocessStartError as exc:
            raise AdbServerBackendAcquireError(str(exc)) from exc

    def _release_handle(
        self,
        handle: _OwnedAdbServerProcess,
    ) -> AdbServerBackendReleaseCleanupUnconfirmed | None:
        try:
            handle.close()
        except Exception as exc:
            return AdbServerBackendReleaseCleanupUnconfirmed(
                handle=handle,
                diagnostic=str(exc).strip() or type(exc).__name__,
            )
        return None


__all__ = ["SubprocessAdbServerBackend"]
