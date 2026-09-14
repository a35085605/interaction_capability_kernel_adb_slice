from __future__ import annotations

from collections.abc import Callable
import os
import socket
import subprocess
from threading import Lock
from time import monotonic, sleep
from typing import Protocol, TypeAlias

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
from adb._subprocess import normalize_executable, normalize_timeout
from adb.aosp.io.server_status import SmartSocketAdbServerStatusReader
from adb.aosp.io.smart_socket import AdbServiceClient
from adb.errors import AdbError, AdbTimeoutError
from networking import TcpEndpoint


_MonotonicClock = Callable[[], float]
_Sleeper = Callable[[float], None]
_PopenFactory = Callable[..., subprocess.Popen[bytes]]
_Resolver = Callable[..., list[tuple[object, ...]]]
_SocketFactory = Callable[[int, int, int], socket.socket]


class _ServerStatusReader(Protocol):
    def read(self, server_endpoint: TcpEndpoint) -> object: ...


class AospAdbServerStartError(RuntimeError):
    """Infrastructure failure while creating a foreground ADB server child."""


class _AospAdbServerRetainedStartError(AospAdbServerStartError):
    """Startup failed while one or more physical resources still require cleanup."""

    def __init__(
        self,
        diagnostic: str,
        resources: tuple[object, ...],
        *,
        original_error: BaseException | None = None,
    ) -> None:
        super().__init__(diagnostic)
        self.resources = resources
        self.original_error = original_error


class AospAdbServerTerminationUnconfirmed(RuntimeError):
    """Failure to confirm termination of an owned child process."""


def _normalize_probe_interval(value: object) -> float:
    normalized = normalize_timeout(value)
    if normalized > 1.0:
        raise ValueError("ADB server startup probe interval must be at most one second")
    return normalized


class AospOwnedAdbServerProcess:
    """One foreground ADB server process whose lifetime is owned by this AOSP driver."""

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
        """Synchronously terminate the owned child and confirm that it exited."""

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
                    raise AospAdbServerTerminationUnconfirmed(
                        f"failed to terminate owned ADB server child process: {exc}"
                    ) from exc

            try:
                self._process.wait(timeout=self._shutdown_timeout_seconds)
            except subprocess.TimeoutExpired:
                try:
                    self._process.kill()
                except OSError as exc:
                    if self._process.poll() is None:
                        raise AospAdbServerTerminationUnconfirmed(
                            f"failed to kill owned ADB server child process: {exc}"
                        ) from exc
                try:
                    self._process.wait(timeout=self._shutdown_timeout_seconds)
                except subprocess.TimeoutExpired as exc:
                    raise AospAdbServerTerminationUnconfirmed(
                        "ADB server child process did not terminate after kill"
                    ) from exc

            if self._process.poll() is None:
                raise AospAdbServerTerminationUnconfirmed(
                    "ADB server child-process termination was not confirmed"
                )
            self._closed = True


AospAdbServerProcessResource: TypeAlias = socket.socket | AospOwnedAdbServerProcess




class AospAdbServerProcessDriver:
    """Synchronous resource driver for one foreground ADB server subprocess.

    Acquisition owns each physical resource as soon as it is created. Temporary listener sockets
    are closed synchronously once the child inherits the descriptor. If acquisition fails while a
    listener or child is still live, those resources are returned with the failure and remain owned
    by the lifecycle until an explicit release retries cleanup.
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

    def acquire(
        self,
        server_endpoint: TcpEndpoint,
    ) -> RequirementAcquireResult[AospAdbServerProcessResource]:
        """Create one ready server process and report every still-owned resource."""

        if not isinstance(server_endpoint, TcpEndpoint):
            raise TypeError("server_endpoint must be TcpEndpoint")

        resources: list[AospAdbServerProcessResource] = []
        try:
            if not self._socket_activation_supported:
                raise AospAdbServerStartError(
                    "ADB acceptfd socket activation is unavailable on this platform; "
                    "a platform-specific server lifecycle implementation is required"
                )

            deadline = Deadline.after(self.startup_timeout_seconds, self._monotonic)
            reservation, resolved_server_endpoint = self._reserve_listener(
                server_endpoint,
                deadline=deadline,
            )
            resources.append(reservation)

            owned_process = self._launch(
                reservation,
                deadline=deadline,
            )
            resources.append(owned_process)

            try:
                reservation.close()
            except BaseException as exc:
                if not isinstance(exc, Exception):
                    raise
                raise AospAdbServerStartError(
                    "ADB server child launched but parent listener reservation close "
                    f"was not confirmed: {exc}"
                ) from exc
            else:
                resources.remove(reservation)

            self._wait_until_ready(
                resolved_server_endpoint,
                owned_process._process,
                deadline=deadline,
            )
        except _AospAdbServerRetainedStartError as exc:
            retained = tuple(resources) + exc.resources
            if exc.original_error is not None and not isinstance(
                exc.original_error, Exception
            ):
                return RequirementAcquireInterrupted(exc.original_error, retained)
            return RequirementAcquireFailed(exc, retained)
        except AospAdbServerStartError as exc:
            return RequirementAcquireFailed(
                exc,
                tuple(resources),
            )
        except BaseException as exc:
            if not isinstance(exc, Exception):
                return RequirementAcquireInterrupted(exc, tuple(resources))
            return RequirementAcquireFailed(exc, tuple(resources))

        return RequirementAcquireSucceeded(tuple(resources))

    def cleanup(
        self,
        resources: PhysicalResources[AospAdbServerProcessResource],
    ) -> None:
        """Synchronously close every retained resource, tolerating cleanup retries."""

        cleanup_reverse(resources, self._close_resource)

    @staticmethod
    def _close_resource(resource: AospAdbServerProcessResource) -> None:
        resource.close()

    def _launch(
        self,
        reservation: socket.socket,
        *,
        deadline: Deadline | float,
    ) -> AospOwnedAdbServerProcess:
        self._check_startup(deadline)
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
            raise AospAdbServerStartError(
                f"failed to launch ADB server child process: {exc}"
            ) from exc

        return AospOwnedAdbServerProcess(process, self.shutdown_timeout_seconds)

    def _reserve_listener(
        self,
        server_endpoint: TcpEndpoint,
        *,
        deadline: Deadline | float,
    ) -> tuple[socket.socket, TcpEndpoint]:
        try:
            addresses = self._resolver.resolve(
                server_endpoint.host,
                server_endpoint.port,
                deadline=deadline,
                cancellation=None,
            )
        except AdbTimeoutError as exc:
            raise AospAdbServerStartError(str(exc)) from exc
        except OSError as exc:
            raise AospAdbServerStartError(
                f"failed to resolve ADB server bind address: {exc}"
            ) from exc
        if not addresses:
            raise AospAdbServerStartError(
                "ADB server bind address resolution returned no candidates"
            )

        failures: list[str] = []
        for address in addresses:
            self._check_startup(deadline)
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
                bound = listener.getsockname()
                resolved = TcpEndpoint(str(bound[0]), int(bound[1]))
                listener.listen(socket.SOMAXCONN)
                return listener, resolved
            except BaseException as exc:
                try:
                    listener.close()
                except BaseException as close_exc:
                    raise _AospAdbServerRetainedStartError(
                        "failed to prepare ADB server listener candidate and could not "
                        f"confirm listener cleanup: {exc}; cleanup error: {close_exc}",
                        (listener,),
                        original_error=(
                            close_exc
                            if not isinstance(close_exc, Exception)
                            else exc
                        ),
                    ) from close_exc

                if not isinstance(exc, Exception):
                    raise
                if isinstance(exc, OSError):
                    failures.append(str(exc))
                    continue
                raise

        detail = "; ".join(failures) or "no bind candidate succeeded"
        raise AospAdbServerStartError(
            f"failed to reserve ADB server listener: {detail}"
        )

    def _coerce_deadline(self, deadline: Deadline | float) -> Deadline:
        if isinstance(deadline, Deadline):
            return deadline
        return Deadline.at(deadline, self._monotonic)

    def _check_startup(self, deadline: Deadline | float) -> None:
        if self._coerce_deadline(deadline).expired():
            raise AospAdbServerStartError("ADB server startup timed out")

    def _wait_until_ready(
        self,
        server_endpoint: TcpEndpoint,
        process: subprocess.Popen[bytes],
        *,
        deadline: Deadline | float | None = None,
    ) -> None:
        if deadline is None:
            deadline = Deadline.after(self.startup_timeout_seconds, self._monotonic)
        else:
            deadline = self._coerce_deadline(deadline)
        last_error: AdbError | None = None
        while True:
            self._check_startup(deadline)

            return_code = process.poll()
            if return_code is not None:
                raise AospAdbServerStartError(
                    f"ADB server child process exited during startup with code {return_code}"
                )

            try:
                self._status_reader.read(server_endpoint)
            except AdbError as exc:
                last_error = exc
            else:
                self._check_startup(deadline)
                if process.poll() is not None:
                    raise AospAdbServerStartError(
                        "ADB server child process exited while startup readiness was being verified"
                    )
                return

            wait_seconds = deadline.clamp(self.probe_interval_seconds)
            if wait_seconds <= 0.0:
                suffix = f": {last_error}" if last_error is not None else ""
                raise AospAdbServerStartError(
                    f"timed out waiting for created ADB server readiness{suffix}"
                )
            self._sleep(wait_seconds)


__all__ = [
    "AospAdbServerProcessDriver",
    "AospAdbServerProcessResource",
    "AospAdbServerStartError",
    "AospAdbServerTerminationUnconfirmed",
    "AospOwnedAdbServerProcess",
]
