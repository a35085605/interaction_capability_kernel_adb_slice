from __future__ import annotations

from collections.abc import Callable
import os
import socket
import subprocess
from threading import Lock
from time import monotonic, sleep
from typing import Protocol, TypeAlias

from _lifecycle_new.resource.driver import (
    PhysicalResources,
    RequirementAcquireFailed,
    RequirementAcquireResult,
    RequirementAcquireSucceeded,
)
from _lifecycle_new.resource.manager import ResolvedResourceProvider
from adb._resolution import DeadlineResolver
from adb.adapters.subprocess.command import normalize_executable, normalize_timeout
from adb.aosp.io.server_status import SmartSocketAdbServerStatusReader
from adb.aosp.io.smart_socket import AdbServiceClient
from adb.errors import AdbError, AdbTimeoutError
from adb.server.access import AdbServerAccess
from adb.server.generation import AdbServerGenerationIssuer
from adb.server.template import AdbServerAcquireError, AdbServerLifecycleTemplate
from networking import TcpAddress


_MonotonicClock = Callable[[], float]
_Sleeper = Callable[[float], None]
_PopenFactory = Callable[..., subprocess.Popen[bytes]]
_Resolver = Callable[..., list[tuple[object, ...]]]
_SocketFactory = Callable[[int, int, int], socket.socket]


class _ServerStatusReader(Protocol):
    def read(self, server_address: TcpAddress) -> object: ...


class _AdbServerSubprocessStartError(RuntimeError):
    """Infrastructure failure while creating a foreground ADB server child."""


class _AdbServerSubprocessRetainedStartError(_AdbServerSubprocessStartError):
    """Startup failed while one or more physical resources still require cleanup."""

    def __init__(
        self,
        diagnostic: str,
        resources: tuple[object, ...],
    ) -> None:
        super().__init__(diagnostic)
        self.resources = resources


class _AdbServerSubprocessTerminationUnconfirmed(RuntimeError):
    """Failure to confirm termination of an owned child process."""


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


_AdbServerSubprocessResource: TypeAlias = socket.socket | _OwnedAdbServerProcess


class _AdbServerSubprocessRequirementsResolver:
    """Resolve one server-access request into its single subprocess requirement."""

    def resolve(self, request: AdbServerAccess) -> tuple[TcpAddress, ...]:
        if not isinstance(request, AdbServerAccess):
            raise TypeError("request must be AdbServerAccess")
        return (request.server_address,)


class _AdbServerSubprocessFactory:
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
        server_address: TcpAddress,
    ) -> RequirementAcquireResult[_AdbServerSubprocessResource]:
        """Create one ready server process and report every still-owned resource."""

        if not isinstance(server_address, TcpAddress):
            raise TypeError("server_address must be TcpAddress")

        resources: list[_AdbServerSubprocessResource] = []
        try:
            if not self._socket_activation_supported:
                raise _AdbServerSubprocessStartError(
                    "ADB acceptfd socket activation is unavailable on this platform; "
                    "a platform-specific server lifecycle implementation is required"
                )

            deadline = self._monotonic() + self.startup_timeout_seconds
            reservation, resolved_server_address = self._reserve_listener(
                server_address,
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
            except Exception as exc:
                raise _AdbServerSubprocessStartError(
                    "ADB server child launched but parent listener reservation close "
                    f"was not confirmed: {exc}"
                ) from exc
            resources.remove(reservation)

            self._wait_until_ready(
                resolved_server_address,
                owned_process._process,
                deadline=deadline,
            )
        except _AdbServerSubprocessRetainedStartError as exc:
            return RequirementAcquireFailed(
                AdbServerAcquireError(str(exc)),
                tuple(resources) + exc.resources,
            )
        except _AdbServerSubprocessStartError as exc:
            return RequirementAcquireFailed(
                AdbServerAcquireError(str(exc)),
                tuple(resources),
            )
        except Exception as exc:
            return RequirementAcquireFailed(exc, tuple(resources))

        return RequirementAcquireSucceeded(tuple(resources))

    def cleanup(
        self,
        resources: PhysicalResources[_AdbServerSubprocessResource],
    ) -> None:
        """Synchronously close every retained resource, tolerating cleanup retries."""

        if not isinstance(resources, tuple):
            raise TypeError("resources must be a PhysicalResources tuple")

        first_error: BaseException | None = None
        for resource in reversed(resources):
            try:
                resource.close()
            except BaseException as exc:
                if first_error is None:
                    first_error = exc

        if first_error is not None:
            raise first_error

    def _launch(
        self,
        reservation: socket.socket,
        *,
        deadline: float,
    ) -> _OwnedAdbServerProcess:
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
            raise _AdbServerSubprocessStartError(
                f"failed to launch ADB server child process: {exc}"
            ) from exc

        return _OwnedAdbServerProcess(process, self.shutdown_timeout_seconds)

    def _reserve_listener(
        self,
        server_address: TcpAddress,
        *,
        deadline: float,
    ) -> tuple[socket.socket, TcpAddress]:
        try:
            addresses = self._resolver.resolve(
                server_address.host,
                server_address.port,
                deadline=deadline,
                cancellation=None,
            )
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
                resolved = TcpAddress(str(bound[0]), int(bound[1]))
                listener.listen(socket.SOMAXCONN)
                return listener, resolved
            except Exception as exc:
                try:
                    listener.close()
                except BaseException as close_exc:
                    raise _AdbServerSubprocessRetainedStartError(
                        "failed to prepare ADB server listener candidate and could not "
                        f"confirm listener cleanup: {exc}; cleanup error: {close_exc}",
                        (listener,),
                    ) from close_exc

                if isinstance(exc, OSError):
                    failures.append(str(exc))
                    continue
                raise

        detail = "; ".join(failures) or "no bind candidate succeeded"
        raise _AdbServerSubprocessStartError(
            f"failed to reserve ADB server listener: {detail}"
        )

    def _check_startup(self, deadline: float) -> None:
        if self._monotonic() >= deadline:
            raise _AdbServerSubprocessStartError("ADB server startup timed out")

    def _wait_until_ready(
        self,
        server_address: TcpAddress,
        process: subprocess.Popen[bytes],
        *,
        deadline: float | None = None,
    ) -> None:
        if deadline is None:
            deadline = self._monotonic() + self.startup_timeout_seconds
        last_error: AdbError | None = None
        while True:
            self._check_startup(deadline)

            return_code = process.poll()
            if return_code is not None:
                raise _AdbServerSubprocessStartError(
                    f"ADB server child process exited during startup with code {return_code}"
                )

            try:
                self._status_reader.read(server_address)
            except AdbError as exc:
                last_error = exc
            else:
                self._check_startup(deadline)
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


class SubprocessAdbServerLifecycle(
    AdbServerLifecycleTemplate[_AdbServerSubprocessResource]
):
    """Provide ADB server access through an owned foreground subprocess.

    The lifecycle is fully synchronous: acquire cannot be revoked by release, and release does not
    return until every retained subprocess resource has been cleaned up or cleanup has failed.
    """

    def __init__(
        self,
        generation_issuer: AdbServerGenerationIssuer,
        *,
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
        if not callable(getattr(_factory, "acquire", None)):
            raise TypeError("_factory must provide acquire()")
        if not callable(getattr(_factory, "cleanup", None)):
            raise TypeError("_factory must provide cleanup()")

        self._factory = _factory
        resource_provider = ResolvedResourceProvider(
            _AdbServerSubprocessRequirementsResolver(),
            _factory,
        )
        super().__init__(generation_issuer, resource_provider)


__all__ = ["SubprocessAdbServerLifecycle"]
