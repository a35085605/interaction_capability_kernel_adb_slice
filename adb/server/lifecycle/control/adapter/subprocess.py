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
from adb.server.identity import AdbServer, AdbServerEpochIssuer
from adb.server.lifecycle.control.port import AdbServerStartError, AdbServerStopError
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


class _SubprocessLifetime:
    """Owned foreground ADB server child process."""

    def __init__(
        self,
        endpoint: AdbServerEndpoint,
        process: subprocess.Popen[bytes],
        shutdown_timeout_seconds: float,
    ) -> None:
        if not isinstance(endpoint, AdbServerEndpoint):
            raise TypeError("endpoint must be AdbServerEndpoint")
        self.endpoint = endpoint
        self._process = process
        self._shutdown_timeout_seconds = shutdown_timeout_seconds
        self._lock = Lock()
        self._closed = False

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
                    raise AdbServerStopError(
                        f"failed to terminate ADB server child process at {self.endpoint.host}:"
                        f"{self.endpoint.port}: {exc}"
                    ) from exc

            try:
                self._process.wait(timeout=self._shutdown_timeout_seconds)
            except subprocess.TimeoutExpired:
                try:
                    self._process.kill()
                except OSError as exc:
                    if self._process.poll() is None:
                        raise AdbServerStopError(
                            f"failed to kill ADB server child process at {self.endpoint.host}:"
                            f"{self.endpoint.port}: {exc}"
                        ) from exc
                try:
                    self._process.wait(timeout=self._shutdown_timeout_seconds)
                except subprocess.TimeoutExpired as exc:
                    raise AdbServerStopError(
                        "ADB server child process did not terminate after kill"
                    ) from exc

            if self._process.poll() is None:
                raise AdbServerStopError(
                    "ADB server child-process termination was not confirmed"
                )
            self._closed = True


class SubprocessAdbServerController:
    """Provide and stop subprocess-backed ADB server lifetimes."""

    def __init__(
        self,
        *,
        server_epoch_issuer: AdbServerEpochIssuer,
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
        if not isinstance(server_epoch_issuer, AdbServerEpochIssuer):
            raise TypeError("server_epoch_issuer must satisfy AdbServerEpochIssuer")
        if not isinstance(_socket_activation_supported, bool):
            raise TypeError("_socket_activation_supported must be a bool")

        self.executable = normalize_executable(executable)
        self.startup_timeout_seconds = normalize_timeout(startup_timeout_seconds)
        self.shutdown_timeout_seconds = normalize_timeout(shutdown_timeout_seconds)
        self.probe_interval_seconds = _normalize_probe_interval(probe_interval_seconds)
        self._server_epoch_issuer = server_epoch_issuer
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

        self._launch_lock = Lock()
        self._lifetimes_lock = Lock()
        # Process handles stay private; stop requests are resolved by AdbServer identity.
        self._lifetimes: dict[AdbServer, _SubprocessLifetime] = {}

    def provide(
        self,
        endpoint: AdbServerEndpoint | None = None,
    ) -> AdbServer:
        if endpoint is not None and not isinstance(endpoint, AdbServerEndpoint):
            raise TypeError("endpoint must be AdbServerEndpoint or None")

        lifetime = self._start_lifetime(endpoint)
        try:
            server = AdbServer(
                lifetime.endpoint,
                self._server_epoch_issuer.issue(),
            )
            with self._lifetimes_lock:
                if server in self._lifetimes:
                    raise RuntimeError("server identity is already bound to a process lifetime")
                self._lifetimes[server] = lifetime
            return server
        except BaseException:
            try:
                lifetime.close()
            except BaseException as stop_error:
                raise AdbServerStartError(
                    "ADB server start failed and its child process could not be stopped"
                ) from stop_error
            raise

    def stop(self, server: AdbServer) -> None:
        if not isinstance(server, AdbServer):
            raise TypeError("server must be AdbServer")

        with self._lifetimes_lock:
            lifetime = self._lifetimes.get(server)
        if lifetime is None:
            raise AdbServerStopError(
                "no exact process lifetime is registered for the requested ADB server"
            )

        lifetime.close()

        with self._lifetimes_lock:
            current = self._lifetimes.get(server)
            if current is lifetime:
                del self._lifetimes[server]

    def _start_lifetime(
        self,
        endpoint: AdbServerEndpoint | None,
    ) -> _SubprocessLifetime:
        if not self._socket_activation_supported:
            raise AdbServerStartError(
                "ADB acceptfd socket activation is unavailable on this platform; "
                "a platform-specific server controller is required"
            )

        with self._launch_lock:
            reservation, resolved_endpoint = self._reserve_listener_locked(endpoint)
            process: subprocess.Popen[bytes] | None = None
            lifetime: _SubprocessLifetime | None = None
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
                lifetime = _SubprocessLifetime(
                    resolved_endpoint,
                    process,
                    self.shutdown_timeout_seconds,
                )
            except OSError as exc:
                raise AdbServerStartError(
                    f"failed to launch ADB server child process: {exc}"
                ) from exc
            finally:
                reservation.close()

            assert process is not None and lifetime is not None
            try:
                self._wait_until_ready(resolved_endpoint, process)
            except BaseException:
                try:
                    lifetime.close()
                except AdbServerStopError as close_exc:
                    raise AdbServerStartError(
                        "ADB server start failed and its child process could not be stopped"
                    ) from close_exc
                raise
            return lifetime

    def _reserve_listener_locked(
        self,
        endpoint: AdbServerEndpoint | None,
    ) -> tuple[socket.socket, AdbServerEndpoint]:
        host = endpoint.host if endpoint is not None else "127.0.0.1"
        port = endpoint.port if endpoint is not None else 0

        try:
            addresses = self._resolver(host, port, type=socket.SOCK_STREAM)
        except OSError as exc:
            raise AdbServerStartError(
                f"failed to resolve ADB server bind address: {exc}"
            ) from exc
        if not addresses:
            raise AdbServerStartError(
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
                resolved = AdbServerEndpoint(str(bound[0]), int(bound[1]))
                return listener, resolved
            except OSError as exc:
                failures.append(str(exc))
                try:
                    listener.close()
                except OSError:
                    pass

        detail = "; ".join(failures) or "no bind candidate succeeded"
        raise AdbServerStartError(f"failed to reserve ADB server listener: {detail}")

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
                raise AdbServerStartError(
                    f"ADB server child process exited during startup with code {return_code}"
                )

            try:
                self._status_reader.read(endpoint)
            except AdbError as exc:
                last_error = exc
            else:
                if process.poll() is not None:
                    raise AdbServerStartError(
                        "ADB server child process exited while startup readiness was being verified"
                    )
                return

            remaining = deadline - self._monotonic()
            if remaining <= 0.0:
                suffix = f": {last_error}" if last_error is not None else ""
                raise AdbServerStartError(
                    f"timed out waiting for created ADB server readiness{suffix}"
                )
            self._sleep(min(self.probe_interval_seconds, remaining))


__all__ = ["SubprocessAdbServerController"]
