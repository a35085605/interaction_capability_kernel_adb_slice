from __future__ import annotations

from collections.abc import Callable
import os
import socket
import subprocess
from threading import Lock
from time import monotonic, sleep
from typing import Protocol

from adb._internal.client import AdbServiceClient
from adb._internal.subprocess import normalize_executable, normalize_timeout
from adb.errors import AdbError
from adb.server.endpoint import AdbServerEndpoint
from adb.server.lifecycle.native import (
    AdbServerCloseError,
    AdbServerLaunchError,
    AdbServerNativeHandle,
)
from adb.server.status.reader import SmartSocketAdbServerStatusReader


_MonotonicClock = Callable[[], float]
_Sleeper = Callable[[float], None]
_PopenFactory = Callable[..., subprocess.Popen[bytes]]
_Resolver = Callable[..., list[tuple[object, ...]]]
_SocketFactory = Callable[[int, int, int], socket.socket]


class _ServerStatusReader(Protocol):
    def read(self, endpoint: AdbServerEndpoint) -> object: ...


def _normalize_probe_interval(value: object) -> float:
    normalized = normalize_timeout(value)
    if normalized > 1.0:
        raise ValueError("ADB server startup probe interval must be at most one second")
    return normalized


class _SubprocessAdbServerHandle:
    """Exact foreground ADB server child process owned by this Python process."""

    def __init__(
        self,
        endpoint: AdbServerEndpoint,
        process: subprocess.Popen[bytes],
        shutdown_timeout_seconds: float,
    ) -> None:
        if not isinstance(endpoint, AdbServerEndpoint):
            raise TypeError("endpoint must be AdbServerEndpoint")
        self._endpoint = endpoint
        self._process = process
        self._shutdown_timeout_seconds = shutdown_timeout_seconds
        self._lock = Lock()
        self._closed = False

    @property
    def endpoint(self) -> AdbServerEndpoint:
        return self._endpoint

    @property
    def active(self) -> bool:
        with self._lock:
            return not self._closed and self._process.poll() is None

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
                    raise AdbServerCloseError(
                        f"failed to terminate owned ADB server at {self.endpoint.host}:"
                        f"{self.endpoint.port}: {exc}"
                    ) from exc

            try:
                self._process.wait(timeout=self._shutdown_timeout_seconds)
            except subprocess.TimeoutExpired:
                try:
                    self._process.kill()
                except OSError as exc:
                    if self._process.poll() is None:
                        raise AdbServerCloseError(
                            f"failed to kill owned ADB server at {self.endpoint.host}:"
                            f"{self.endpoint.port}: {exc}"
                        ) from exc
                try:
                    self._process.wait(timeout=self._shutdown_timeout_seconds)
                except subprocess.TimeoutExpired as exc:
                    raise AdbServerCloseError(
                        "owned ADB server did not terminate after kill"
                    ) from exc

            if self._process.poll() is None:
                raise AdbServerCloseError("owned ADB server termination was not confirmed")
            self._closed = True


class SubprocessAdbServerLauncher:
    """Launch a foreground ADB server from an OS-owned listening socket.

    On POSIX, ADB's ``acceptfd:`` socket activation path lets this process bind and listen
    before spawning ``adb server nodaemon``. The inherited socket and foreground child process
    form the native ownership authority: an already-running listener can never satisfy launch.

    The first successful launch may let the OS select an ephemeral loopback port. That resolved
    endpoint is retained by this launcher for later generations so recovery keeps one endpoint
    without storing endpoint lineage in the process-owner state machine.
    """

    def __init__(
        self,
        endpoint: AdbServerEndpoint | None = None,
        *,
        executable: str = "adb",
        startup_timeout_seconds: float = 5.0,
        shutdown_timeout_seconds: float = 5.0,
        probe_interval_seconds: float = 0.05,
        _popen_factory: _PopenFactory = subprocess.Popen,
        _resolver: _Resolver = socket.getaddrinfo,
        _socket_factory: _SocketFactory = socket.socket,
        _monotonic: _MonotonicClock = monotonic,
        _sleep: _Sleeper = sleep,
        _status_reader: _ServerStatusReader | None = None,
        _socket_activation_supported: bool = os.name != "nt",
    ) -> None:
        if endpoint is not None and not isinstance(endpoint, AdbServerEndpoint):
            raise TypeError("endpoint must be AdbServerEndpoint or None")
        if not isinstance(_socket_activation_supported, bool):
            raise TypeError("_socket_activation_supported must be a bool")
        self.executable = normalize_executable(executable)
        self.startup_timeout_seconds = normalize_timeout(startup_timeout_seconds)
        self.shutdown_timeout_seconds = normalize_timeout(shutdown_timeout_seconds)
        self.probe_interval_seconds = _normalize_probe_interval(probe_interval_seconds)
        self._endpoint = endpoint
        self._popen_factory = _popen_factory
        self._resolver = _resolver
        self._socket_factory = _socket_factory
        self._monotonic = _monotonic
        self._sleep = _sleep
        self._socket_activation_supported = _socket_activation_supported
        if _status_reader is None:
            read_timeout = min(0.25, self.startup_timeout_seconds)
            _status_reader = SmartSocketAdbServerStatusReader(
                _client_factory=lambda candidate: AdbServiceClient(
                    candidate,
                    timeout_seconds=read_timeout,
                )
            )
        if not callable(getattr(_status_reader, "read", None)):
            raise TypeError("_status_reader must provide read()")
        self._status_reader = _status_reader
        self._lock = Lock()

    @property
    def endpoint(self) -> AdbServerEndpoint | None:
        """Return the endpoint pinned by the first successful launch, if any."""

        with self._lock:
            return self._endpoint

    def launch(self) -> AdbServerNativeHandle:
        if not self._socket_activation_supported:
            raise AdbServerLaunchError(
                "ADB acceptfd socket activation is unavailable on this platform; "
                "a platform-specific owned-server launcher is required"
            )

        with self._lock:
            reservation, endpoint = self._reserve_listener_locked()
            process: subprocess.Popen[bytes] | None = None
            handle: _SubprocessAdbServerHandle | None = None
            try:
                fd = reservation.fileno()
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
                handle = _SubprocessAdbServerHandle(
                    endpoint,
                    process,
                    self.shutdown_timeout_seconds,
                )
            except OSError as exc:
                raise AdbServerLaunchError(
                    f"failed to launch owned ADB server process: {exc}"
                ) from exc
            finally:
                reservation.close()

            assert process is not None and handle is not None
            try:
                self._wait_until_ready(endpoint, process)
            except BaseException:
                try:
                    handle.close()
                except AdbServerCloseError as close_exc:
                    raise AdbServerLaunchError(
                        "ADB server launch failed and its child process could not be closed"
                    ) from close_exc
                raise

            # Pin only after this exact child has reached ADB protocol readiness.
            if self._endpoint is None:
                self._endpoint = endpoint
            return handle

    def _reserve_listener_locked(self) -> tuple[socket.socket, AdbServerEndpoint]:
        configured = self._endpoint
        host = configured.host if configured is not None else "127.0.0.1"
        port = configured.port if configured is not None else 0

        try:
            addresses = self._resolver(host, port, type=socket.SOCK_STREAM)
        except OSError as exc:
            raise AdbServerLaunchError(f"failed to resolve ADB server bind address: {exc}") from exc
        if not addresses:
            raise AdbServerLaunchError("ADB server bind address resolution returned no candidates")

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
                endpoint = AdbServerEndpoint(str(bound[0]), int(bound[1]))
                return listener, endpoint
            except OSError as exc:
                failures.append(str(exc))
                try:
                    listener.close()
                except OSError:
                    pass

        detail = "; ".join(failures) or "no bind candidate succeeded"
        raise AdbServerLaunchError(f"failed to reserve owned ADB server listener: {detail}")

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
                raise AdbServerLaunchError(
                    f"owned ADB server exited during startup with code {return_code}"
                )

            try:
                self._status_reader.read(endpoint)
            except AdbError as exc:
                last_error = exc
            else:
                if process.poll() is not None:
                    raise AdbServerLaunchError(
                        "owned ADB server exited while startup readiness was being verified"
                    )
                return

            remaining = deadline - self._monotonic()
            if remaining <= 0.0:
                suffix = f": {last_error}" if last_error is not None else ""
                raise AdbServerLaunchError(
                    f"timed out waiting for owned ADB server readiness{suffix}"
                )
            self._sleep(min(self.probe_interval_seconds, remaining))


__all__ = ["SubprocessAdbServerLauncher"]
