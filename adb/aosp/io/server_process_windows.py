from __future__ import annotations

from collections.abc import Callable
import os
import shutil
import socket
from time import monotonic, sleep
from typing import Protocol, TypeAlias

from _lifecycle_new.resource.cleanup import cleanup_reverse
from _lifecycle_new.resource.driver import (
    PhysicalResources,
    RequirementAcquireFailed,
    RequirementAcquireInterrupted,
    RequirementAcquireResult,
    RequirementAcquireSucceeded,
)
from adb._deadline import Deadline
from adb._subprocess import normalize_executable, normalize_timeout
from adb.aosp.io.server_status import SmartSocketAdbServerStatusReader
from adb.aosp.io.smart_socket import AdbServiceClient
from adb.aosp.io.server_process import AospAdbServerStartError
from adb.errors import AdbError
from networking import TcpEndpoint


if os.name != "nt":
    raise ImportError("server_process_windows is only available on Windows")

from windows.process_lifecycle import (
    WindowsOwnedProcess,
    WindowsProcessLifecycleManager,
    WindowsProcessSpec,
    WindowsProcessStartError,
    WindowsRetainedProcess,
)
from windows.tcp_listener import WindowsTcpListenerTable, WindowsTcpTableError


_LOOPBACK_IPV4 = "127.0.0.1"
_WILDCARD_IPV4 = "0.0.0.0"

_MonotonicClock = Callable[[], float]
_Sleeper = Callable[[float], None]
_SocketFactory = Callable[[int, int, int], socket.socket]
_ExecutableResolver = Callable[[str], str | None]


class _ServerStatusReader(Protocol):
    def read(self, server_endpoint: TcpEndpoint) -> object: ...


class _ListenerTable(Protocol):
    def read(self) -> tuple[object, ...]: ...


class _WindowsAospAdbServerStartError(AospAdbServerStartError):
    """Infrastructure failure while establishing the Windows ADB server."""


class _WindowsAospAdbServerRetainedStartError(_WindowsAospAdbServerStartError):
    """Startup failed while one or more resources still require cleanup."""

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


def _normalize_probe_interval(value: object) -> float:
    normalized = normalize_timeout(value)
    if normalized > 1.0:
        raise ValueError("ADB server startup probe interval must be at most one second")
    return normalized


def _resolve_executable(
    executable: str,
    resolver: _ExecutableResolver,
) -> str:
    normalized = normalize_executable(executable)
    resolved = resolver(normalized)
    if resolved is None:
        raise _WindowsAospAdbServerStartError(
            f"ADB executable could not be resolved through PATH/PATHEXT: {normalized!r}"
        )

    absolute = os.path.abspath(resolved)
    if not os.path.isfile(absolute):
        raise _WindowsAospAdbServerStartError(
            f"resolved ADB executable is not a file: {absolute!r}"
        )
    if not absolute.casefold().endswith(".exe"):
        raise _WindowsAospAdbServerStartError(
            "resolved ADB executable must be a Windows .exe when passed as "
            f"CreateProcess application name: {absolute!r}"
        )
    return absolute


WindowsAospAdbServerProcessResource: TypeAlias = (
    socket.socket | WindowsOwnedProcess | WindowsRetainedProcess
)


class WindowsAospAdbServerProcessDriver:
    """Own one Windows foreground ADB server and verify its listener identity.

    Windows cannot use ADB's ``acceptfd:`` listener handoff. Acquisition therefore
    performs an exclusive IPv4 loopback reservation as a preflight check, releases
    it, starts ``adb server nodaemon -L tcp:localhost:PORT`` under a private Job,
    and then verifies through the Windows TCP owner-PID table that the ready
    listener belongs to the owned root process.
    """

    def __init__(
        self,
        *,
        executable: str = "adb",
        startup_timeout_seconds: float = 5.0,
        shutdown_timeout_seconds: float = 5.0,
        probe_interval_seconds: float = 0.05,
        status_reader: _ServerStatusReader | None = None,
        process_manager: WindowsProcessLifecycleManager | None = None,
        listener_table: WindowsTcpListenerTable | None = None,
        socket_factory: _SocketFactory = socket.socket,
        executable_resolver: _ExecutableResolver = shutil.which,
        monotonic_clock: _MonotonicClock = monotonic,
        sleeper: _Sleeper = sleep,
    ) -> None:
        self.executable = normalize_executable(executable)
        self.startup_timeout_seconds = normalize_timeout(startup_timeout_seconds)
        self.shutdown_timeout_seconds = normalize_timeout(shutdown_timeout_seconds)
        self.probe_interval_seconds = _normalize_probe_interval(probe_interval_seconds)
        self._process_manager = process_manager or WindowsProcessLifecycleManager()
        self._listener_table = listener_table or WindowsTcpListenerTable()
        self._socket_factory = socket_factory
        self._executable_resolver = executable_resolver
        self._monotonic = monotonic_clock
        self._sleep = sleeper

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
        if not callable(getattr(self._process_manager, "spawn", None)):
            raise TypeError("process_manager must provide spawn()")
        if not callable(getattr(self._listener_table, "read", None)):
            raise TypeError("listener_table must provide read()")
        if not callable(socket_factory):
            raise TypeError("socket_factory must be callable")
        if not callable(executable_resolver):
            raise TypeError("executable_resolver must be callable")
        self._status_reader = status_reader

    def acquire(
        self,
        server_endpoint: TcpEndpoint,
    ) -> RequirementAcquireResult[WindowsAospAdbServerProcessResource]:
        """Create a verified loopback ADB server and retain all owned failures."""

        if not isinstance(server_endpoint, TcpEndpoint):
            raise TypeError("server_endpoint must be TcpEndpoint")

        resources: list[WindowsAospAdbServerProcessResource] = []
        try:
            self._validate_endpoint(server_endpoint)
            deadline = Deadline.after(self.startup_timeout_seconds, self._monotonic)
            executable = _resolve_executable(
                self.executable,
                self._executable_resolver,
            )

            reservation = self._reserve_listener(server_endpoint)
            resources.append(reservation)
            try:
                reservation.close()
            except BaseException:
                # The socket remains lifecycle-owned until retry cleanup can
                # confirm its close. Do not start a process while reservation
                # ownership itself is uncertain.
                raise
            else:
                resources.remove(reservation)

            self._check_startup(deadline)
            try:
                owned_process = self._process_manager.spawn(
                    WindowsProcessSpec(
                        argv=(
                            executable,
                            "server",
                            "nodaemon",
                            "-L",
                            f"tcp:localhost:{server_endpoint.port}",
                        ),
                        executable=executable,
                    )
                )
            except WindowsProcessStartError as exc:
                if exc.retained_process is not None:
                    resources.append(exc.retained_process)

                original_error = exc.original_error
                if original_error is not None and not isinstance(
                    original_error, Exception
                ):
                    return RequirementAcquireInterrupted(
                        original_error,
                        tuple(resources),
                    )

                raise _WindowsAospAdbServerStartError(str(exc)) from exc

            resources.append(owned_process)
            self._wait_until_ready_and_owned(
                server_endpoint,
                owned_process,
                deadline=deadline,
            )
        except _WindowsAospAdbServerRetainedStartError as exc:
            resources.extend(exc.resources)
            if exc.original_error is not None and not isinstance(
                exc.original_error, Exception
            ):
                return RequirementAcquireInterrupted(
                    exc.original_error,
                    tuple(resources),
                )
            return RequirementAcquireFailed(
                exc,
                tuple(resources),
            )
        except _WindowsAospAdbServerStartError as exc:
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
        resources: PhysicalResources[WindowsAospAdbServerProcessResource],
    ) -> None:
        """Clean every owned resource; failures preserve retryable ownership."""

        cleanup_reverse(resources, self._close_resource)

    def _close_resource(self, resource: WindowsAospAdbServerProcessResource) -> None:
        if isinstance(resource, (WindowsOwnedProcess, WindowsRetainedProcess)):
            resource.close(self.shutdown_timeout_seconds)
            return
        close = getattr(resource, "close", None)
        if not callable(close):
            raise TypeError(
                "unsupported Windows ADB server resource: "
                f"{type(resource).__name__}"
            )
        close()

    @staticmethod
    def _validate_endpoint(server_endpoint: TcpEndpoint) -> None:
        if server_endpoint.host != _LOOPBACK_IPV4:
            raise _WindowsAospAdbServerStartError(
                "Windows ADB server lifecycle currently supports only the "
                f"IPv4 loopback endpoint {_LOOPBACK_IPV4!r}; got "
                f"{server_endpoint.host!r}"
            )

    def _reserve_listener(self, server_endpoint: TcpEndpoint) -> socket.socket:
        try:
            listener = self._socket_factory(
                socket.AF_INET,
                socket.SOCK_STREAM,
                socket.IPPROTO_TCP,
            )
        except OSError as exc:
            raise _WindowsAospAdbServerStartError(
                f"failed to create Windows ADB listener reservation socket: {exc}"
            ) from exc

        try:
            exclusive = getattr(socket, "SO_EXCLUSIVEADDRUSE", None)
            if exclusive is None:
                raise _WindowsAospAdbServerStartError(
                    "SO_EXCLUSIVEADDRUSE is unavailable on this Windows runtime"
                )
            listener.setsockopt(socket.SOL_SOCKET, exclusive, 1)
            listener.bind((server_endpoint.host, server_endpoint.port))
            listener.listen(1)
            return listener
        except BaseException as exc:
            try:
                listener.close()
            except BaseException as close_exc:
                raise _WindowsAospAdbServerRetainedStartError(
                    "failed to prepare the Windows ADB listener reservation and "
                    f"could not confirm socket cleanup: {exc}; cleanup error: {close_exc}",
                    (listener,),
                    original_error=exc,
                ) from close_exc

            if isinstance(exc, _WindowsAospAdbServerStartError):
                raise
            if not isinstance(exc, Exception):
                raise
            raise _WindowsAospAdbServerStartError(
                f"failed to reserve Windows ADB endpoint: {exc}"
            ) from exc

    def _read_listener_rows(self) -> tuple[object, ...]:
        try:
            return self._listener_table.read()
        except WindowsTcpTableError as exc:
            raise _WindowsAospAdbServerStartError(
                f"failed to read Windows TCP listener ownership: {exc}"
            ) from exc

    def _owned_listener_observed(
        self,
        server_endpoint: TcpEndpoint,
        owned_pid: int,
    ) -> bool:
        relevant: list[object] = []
        for row in self._read_listener_rows():
            address = getattr(row, "address", None)
            port = getattr(row, "port", None)
            pid = getattr(row, "pid", None)
            if port != server_endpoint.port:
                continue
            if address not in (server_endpoint.host, _WILDCARD_IPV4):
                continue
            relevant.append(row)
            if pid != owned_pid or address != server_endpoint.host:
                raise _WindowsAospAdbServerStartError(
                    "ADB server endpoint is owned by a different or unexpected "
                    f"Windows listener (address={address!r}, port={port!r}, pid={pid!r})"
                )

        return any(
            getattr(row, "address", None) == server_endpoint.host
            and getattr(row, "pid", None) == owned_pid
            for row in relevant
        )

    def _wait_until_ready_and_owned(
        self,
        server_endpoint: TcpEndpoint,
        owned_process: WindowsOwnedProcess,
        *,
        deadline: Deadline | float,
    ) -> None:
        deadline = self._coerce_deadline(deadline)
        while True:
            self._check_startup(deadline)
            if owned_process.poll() is not None:
                raise _WindowsAospAdbServerStartError(
                    "owned ADB root process exited during startup"
                )

            if not self._owned_listener_observed(server_endpoint, owned_process.pid):
                self._sleep_until_retry(deadline)
                continue

            try:
                self._status_reader.read(server_endpoint)
            except AdbError as exc:
                self._sleep_until_retry(deadline, last_error=exc)
                continue

            self._check_startup(deadline)
            if owned_process.poll() is not None:
                raise _WindowsAospAdbServerStartError(
                    "owned ADB root process exited while readiness was being verified"
                )
            if not self._owned_listener_observed(server_endpoint, owned_process.pid):
                raise _WindowsAospAdbServerStartError(
                    "ADB listener ownership changed during readiness verification"
                )
            return

    def _sleep_until_retry(
        self,
        deadline: Deadline | float,
        *,
        last_error: AdbError | None = None,
    ) -> None:
        deadline = self._coerce_deadline(deadline)
        wait_seconds = deadline.clamp(self.probe_interval_seconds)
        if wait_seconds <= 0.0:
            suffix = f": {last_error}" if last_error is not None else ""
            raise _WindowsAospAdbServerStartError(
                f"timed out waiting for the owned ADB server listener{suffix}"
            )
        self._sleep(wait_seconds)

    def _coerce_deadline(self, deadline: Deadline | float) -> Deadline:
        if isinstance(deadline, Deadline):
            return deadline
        return Deadline.at(deadline, self._monotonic)

    def _check_startup(self, deadline: Deadline | float) -> None:
        if self._coerce_deadline(deadline).expired():
            raise _WindowsAospAdbServerStartError("ADB server startup timed out")


__all__ = ["WindowsAospAdbServerProcessDriver"]
