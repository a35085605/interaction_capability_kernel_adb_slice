from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from ipaddress import ip_address
import os
import socket
import subprocess
from threading import Event, Lock
from time import monotonic, sleep
from typing import Protocol

from adb._lifecycle import ResourceOwnership, ResourceScope
from adb._resolution import AddressResolutionCancelled, DeadlineResolver
from adb.adapters.subprocess.command import normalize_executable, normalize_timeout
from adb.errors import AdbError, AdbTimeoutError
from adb.aosp.io.smart_socket import AdbServiceClient
from networking import TcpAddress
from adb.server.generation import AdbServerGenerationIssuer
from adb.cleanup import CleanupHandoff
from adb.server.template import (
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
    def read(self, endpoint: TcpAddress) -> object: ...


class _AdbServerSubprocessStartError(RuntimeError):
    """Infrastructure failure while creating a foreground ADB server child."""


class _AdbServerSubprocessAcquireInterrupted(RuntimeError):
    """Startup was interrupted after its server authority was released."""


class _AdbServerSubprocessTerminationUnconfirmed(RuntimeError):
    """Failure to confirm termination of an owned child process."""


@dataclass(frozen=True, slots=True)
class _TcpBindClaim:
    """Adapter-private claim for a local TCP bind exclusivity domain."""

    family: int | None
    address: str | None
    port: int


def _normalize_claim_address(host: str) -> str | None:
    """Normalize an IP bind address; ``None`` means wildcard or unresolved/unknown."""

    candidate = host.split("%", 1)[0]
    try:
        address = ip_address(candidate)
    except ValueError:
        return None
    if address.is_unspecified:
        return None
    return str(address)


def _bind_claims_conflict(existing: _TcpBindClaim, requested: _TcpBindClaim) -> bool:
    if existing.port != requested.port:
        return False
    if (
        existing.family is not None
        and requested.family is not None
        and existing.family != requested.family
    ):
        # Different concrete protocol families are independent unless either side is wildcard or
        # otherwise unknown (for example an IPv6 dual-stack wildcard listener).
        return existing.address is None or requested.address is None
    if existing.address is None or requested.address is None:
        return True
    return existing.address == requested.address


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


def _cleanup_owned_process(process: _OwnedAdbServerProcess) -> object | None:
    try:
        process.close()
    except BaseException:
        return process.unresolved_cleanup_resource()
    return None


class _AdbServerSubprocessFactory:
    """Create ready foreground ADB server access while populating a resource scope.

    The listener and child process are adopted at the moment they are obtained. The parent listener
    ownership is retired only after its close succeeds. The child process retains the listener bind
    claim because the inherited descriptor may keep that bind occupied after the parent handle is
    closed. Startup failure therefore needs no synthetic cleanup exception: the acquisition scope
    already contains every still-owned resource and its claims.
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
        endpoint: TcpAddress,
        cancellation: Event,
        resources: ResourceScope,
    ) -> TcpAddress:
        if not isinstance(endpoint, TcpAddress):
            raise TypeError("endpoint must be TcpAddress")
        if not isinstance(cancellation, Event):
            raise TypeError("cancellation must be threading.Event")
        if not isinstance(resources, ResourceScope):
            raise TypeError("resources must be ResourceScope")
        if cancellation.is_set():
            raise _AdbServerSubprocessAcquireInterrupted
        if not self._socket_activation_supported:
            raise _AdbServerSubprocessStartError(
                "ADB acceptfd socket activation is unavailable on this platform; "
                "a platform-specific server lifecycle implementation is required"
            )

        deadline = self._monotonic() + self.startup_timeout_seconds
        owned_process, resolved_endpoint = self._launch(
            endpoint,
            resources,
            deadline=deadline,
            cancellation=cancellation,
        )
        if cancellation.is_set():
            raise _AdbServerSubprocessAcquireInterrupted
        self._wait_until_ready(
            resolved_endpoint,
            owned_process._process,
            cancellation=cancellation,
            deadline=deadline,
        )
        return resolved_endpoint

    def _launch(
        self,
        endpoint: TcpAddress,
        resources: ResourceScope,
        *,
        deadline: float,
        cancellation: Event,
    ) -> tuple[_OwnedAdbServerProcess, TcpAddress]:
        reservation, reservation_ownership, resolved_endpoint = self._reserve_listener(
            endpoint,
            resources,
            deadline=deadline,
            cancellation=cancellation,
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
            if _close_socket_or_cleanup_resource(reservation) is None:
                resources.release(reservation_ownership)
            if primary is exc:
                raise
            raise primary from exc

        owned_process = _OwnedAdbServerProcess(process, self.shutdown_timeout_seconds)
        resources.adopt(
            owned_process,
            lambda: _cleanup_owned_process(owned_process),
            claims=reservation_ownership.claims,
        )

        if _close_socket_or_cleanup_resource(reservation) is None:
            resources.release(reservation_ownership)
            return owned_process, resolved_endpoint

        raise _AdbServerSubprocessStartError(
            "ADB server child launched but parent listener reservation cleanup was not confirmed"
        )

    def _reserve_listener(
        self,
        endpoint: TcpAddress,
        resources: ResourceScope,
        *,
        deadline: float,
        cancellation: Event,
    ) -> tuple[socket.socket, ResourceOwnership, TcpAddress]:
        try:
            addresses = self._resolver.resolve(
                endpoint.host,
                endpoint.port,
                deadline=deadline,
                cancellation=cancellation,
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

            ownership = resources.adopt(
                listener,
                lambda listener=listener: _close_socket_or_cleanup_resource(listener),
            )
            try:
                listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                listener.bind(sockaddr)
                bound = listener.getsockname()
                resolved = TcpAddress(str(bound[0]), int(bound[1]))
                resources.replace_claims(
                    ownership,
                    (
                        _TcpBindClaim(
                            family=family,
                            address=_normalize_claim_address(resolved.host),
                            port=resolved.port,
                        ),
                    ),
                )
                listener.listen(socket.SOMAXCONN)
                return listener, ownership, resolved
            except BaseException as exc:
                primary: BaseException = (
                    _AdbServerSubprocessStartError(
                        f"failed to prepare ADB server listener candidate: {exc}"
                    )
                    if isinstance(exc, OSError)
                    else exc
                )
                if _close_socket_or_cleanup_resource(listener) is None:
                    resources.release(ownership)
                else:
                    if primary is exc:
                        raise
                    raise primary from exc
                if primary is exc:
                    raise
                failures.append(str(exc))

        detail = "; ".join(failures) or "no bind candidate succeeded"
        raise _AdbServerSubprocessStartError(
            f"failed to reserve ADB server listener: {detail}"
        )

    def _check_startup(self, deadline: float, cancellation: Event) -> None:
        if cancellation.is_set():
            raise _AdbServerSubprocessAcquireInterrupted
        if self._monotonic() >= deadline:
            raise _AdbServerSubprocessStartError("ADB server startup timed out")

    def _wait_until_ready(
        self,
        endpoint: TcpAddress,
        process: subprocess.Popen[bytes],
        *,
        cancellation: Event,
        deadline: float | None = None,
    ) -> None:
        if not isinstance(cancellation, Event):
            raise TypeError("cancellation must be threading.Event")
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
            if cancellation.wait(delay):
                raise _AdbServerSubprocessAcquireInterrupted


class SubprocessAdbServerLifecycle(AdbServerLifecycleTemplate):
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

    def _obtain_access(
        self,
        endpoint: TcpAddress,
        cancellation: Event,
        resources: ResourceScope,
    ) -> TcpAddress:
        try:
            return self._factory.create(endpoint, cancellation, resources)
        except _AdbServerSubprocessAcquireInterrupted as exc:
            raise AdbServerAcquireInterruptedError() from exc
        except _AdbServerSubprocessStartError as exc:
            raise AdbServerAcquireError(str(exc)) from exc

    def _requested_resource_claims(
        self,
        endpoint: TcpAddress,
    ) -> tuple[object, ...]:
        return (
            _TcpBindClaim(
                family=None,
                address=_normalize_claim_address(endpoint.host),
                port=endpoint.port,
            ),
        )

    def _resource_claims_conflict(self, existing: object, requested: object) -> bool:
        if not isinstance(existing, _TcpBindClaim) or not isinstance(requested, _TcpBindClaim):
            raise TypeError("subprocess ADB server resource claims must be TCP bind claims")
        return _bind_claims_conflict(existing, requested)


__all__ = ["SubprocessAdbServerLifecycle"]
