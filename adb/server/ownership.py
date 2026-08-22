from __future__ import annotations

from collections.abc import Callable
from enum import Enum
from threading import Condition

from adb.server.identity import AdbServer
from adb.server.lifecycle.handle import AdbServerNativeHandle
from adb.server.lifecycle.launch import AdbServerLauncher
from adb.server.endpoint import AdbServerEndpoint


_OWNED_SERVER_CONSTRUCTION_TOKEN = object()


class AdbServerOwnershipLostError(RuntimeError):
    """The process has no currently usable owned ADB server lifetime."""


class AdbServerStaleOwnerError(AdbServerOwnershipLostError):
    """An ownership operation referenced an ADB server that is no longer current."""


class _AdbOwnedServer:
    """Private exact-lifetime ownership record for one :class:`AdbServer`."""

    __slots__ = ("server",)

    def __init__(self, server: AdbServer, *, _token: object) -> None:
        if _token is not _OWNED_SERVER_CONSTRUCTION_TOKEN:
            raise TypeError("owned ADB server records are created by the lifetime store")
        if not isinstance(server, AdbServer):
            raise TypeError("server must be AdbServer")
        self.server = server

    @classmethod
    def _new(cls, server: AdbServer) -> "_AdbOwnedServer":
        return cls(server, _token=_OWNED_SERVER_CONSTRUCTION_TOKEN)


class _OwnedServerLifetime:
    """Private exact-lifetime authority backing one server identity."""

    __slots__ = ("owner", "native")

    def __init__(self, owner: _AdbOwnedServer, native: AdbServerNativeHandle) -> None:
        self.owner = owner
        self.native = native


class _DefaultAdbServerLauncher:
    """Lazily construct the concrete launcher after the ADB package graph is imported."""

    def __init__(self) -> None:
        self._delegate: AdbServerLauncher | None = None

    def launch(self, endpoint: AdbServerEndpoint | None = None) -> AdbServerNativeHandle:
        delegate = self._delegate
        if delegate is None:
            from adb.server.lifecycle.subprocess import SubprocessAdbServerLauncher

            delegate = SubprocessAdbServerLauncher()
            self._delegate = delegate
        return delegate.launch(endpoint)


class _OwnedAdbServerStoreStatus(str, Enum):
    ABSENT = "absent"
    STARTING = "starting"
    ACTIVE = "active"


class _RetiredServerLifetimeStatus(str, Enum):
    CLOSING = "closing"
    CLOSE_UNPROVEN = "close_unproven"


class _RetiredServerLifetime:
    """Private teardown state for one irreversibly retired server."""

    __slots__ = ("lifetime", "status", "close_failure")

    def __init__(self, lifetime: _OwnedServerLifetime) -> None:
        self.lifetime = lifetime
        self.status = _RetiredServerLifetimeStatus.CLOSING
        self.close_failure: BaseException | None = None


class _OwnedAdbServerLifetimeStore:
    """Serialize exact owned lifetimes while exposing only :class:`AdbServer` identities.

    Native handles and ownership records never leave this store. Process singleton scope,
    exclusive mutation leases, epoch generation, and supervision policy live above it in
    ``adb.server.coordination``.
    """

    def __init__(self, launcher: AdbServerLauncher | None = None) -> None:
        if launcher is None:
            launcher = _DefaultAdbServerLauncher()
        elif not isinstance(launcher, AdbServerLauncher):
            raise TypeError("launcher must satisfy AdbServerLauncher")
        self._launcher = launcher
        self._condition = Condition()
        self._status = _OwnedAdbServerStoreStatus.ABSENT
        self._active_lifetime: _OwnedServerLifetime | None = None
        self._retired_lifetimes: dict[AdbServer, _RetiredServerLifetime] = {}

    def acquire(
        self,
        endpoint: AdbServerEndpoint | None = None,
        *,
        server_factory: Callable[[AdbServerEndpoint], AdbServer],
    ) -> AdbServer:
        """Return the active server or launch one fresh exact native lifetime."""

        if endpoint is not None and not isinstance(endpoint, AdbServerEndpoint):
            raise TypeError("endpoint must be AdbServerEndpoint or None")
        if not callable(server_factory):
            raise TypeError("server_factory must be callable")

        with self._condition:
            while self._status is _OwnedAdbServerStoreStatus.STARTING:
                self._condition.wait()

            if self._status is _OwnedAdbServerStoreStatus.ACTIVE:
                assert self._active_lifetime is not None
                return self._active_lifetime.owner.server

            assert self._status is _OwnedAdbServerStoreStatus.ABSENT
            self._status = _OwnedAdbServerStoreStatus.STARTING

        try:
            native = self._launcher.launch(endpoint)
            if not isinstance(native, AdbServerNativeHandle):
                raise TypeError("launcher.launch() must return AdbServerNativeHandle")
        except BaseException:
            with self._condition:
                self._status = _OwnedAdbServerStoreStatus.ABSENT
                self._active_lifetime = None
                self._condition.notify_all()
            raise

        try:
            server = server_factory(native.endpoint)
            if not isinstance(server, AdbServer):
                raise TypeError("server_factory must return AdbServer")
            if server.endpoint != native.endpoint:
                raise ValueError("server endpoint must match launched native endpoint")
            owner = _AdbOwnedServer._new(server)
        except BaseException as server_error:
            try:
                native.close()
            except BaseException as close_error:
                with self._condition:
                    self._status = _OwnedAdbServerStoreStatus.ABSENT
                    self._active_lifetime = None
                    self._condition.notify_all()
                raise close_error from server_error
            with self._condition:
                self._status = _OwnedAdbServerStoreStatus.ABSENT
                self._active_lifetime = None
                self._condition.notify_all()
            raise

        with self._condition:
            self._active_lifetime = _OwnedServerLifetime(owner, native)
            self._status = _OwnedAdbServerStoreStatus.ACTIVE
            self._condition.notify_all()
            return server

    def retire(self, server: AdbServer) -> bool:
        """Irreversibly withdraw one server from the active projection."""

        self._require_server(server)
        with self._condition:
            if server in self._retired_lifetimes:
                return False

            lifetime = self._active_lifetime
            if lifetime is None:
                return False
            if lifetime.owner.server != server:
                return False
            if self._status is not _OwnedAdbServerStoreStatus.ACTIVE:
                raise self._stale_server_error(server)

            self._active_lifetime = None
            self._retired_lifetimes[server] = _RetiredServerLifetime(lifetime)
            self._status = _OwnedAdbServerStoreStatus.ABSENT
            self._condition.notify_all()
            return True

    def dispose_retired(self, server: AdbServer) -> None:
        """Prove native termination for one already-retired server."""

        self._require_server(server)
        with self._condition:
            retired = self._retired_lifetimes.get(server)
            if retired is None:
                raise self._stale_server_error(server)
            retired.status = _RetiredServerLifetimeStatus.CLOSING
            retired.close_failure = None
            native = retired.lifetime.native

        try:
            native.close()
        except BaseException as exc:
            with self._condition:
                current = self._retired_lifetimes.get(server)
                if current is retired:
                    retired.status = _RetiredServerLifetimeStatus.CLOSE_UNPROVEN
                    retired.close_failure = exc
                    self._condition.notify_all()
            raise

        with self._condition:
            current = self._retired_lifetimes.get(server)
            if current is retired:
                del self._retired_lifetimes[server]
                self._condition.notify_all()

    def invalidate(self, server: AdbServer) -> bool:
        """Retire and synchronously dispose one server after liveness loss."""

        retired_now = self.retire(server)
        with self._condition:
            can_dispose = server in self._retired_lifetimes
        if not can_dispose:
            return False
        self.dispose_retired(server)
        return retired_now or can_dispose

    def close(self, server: AdbServer) -> None:
        """Retire and synchronously close one exact server lifetime."""

        retired_now = self.retire(server)
        if not retired_now:
            with self._condition:
                if server not in self._retired_lifetimes:
                    raise self._stale_server_error(server)
        self.dispose_retired(server)

    @property
    def active_server(self) -> AdbServer | None:
        """Return the active server identity without launching a new lifetime."""

        with self._condition:
            if self._status is not _OwnedAdbServerStoreStatus.ACTIVE:
                return None
            assert self._active_lifetime is not None
            return self._active_lifetime.owner.server

    @staticmethod
    def _require_server(server: object) -> None:
        if not isinstance(server, AdbServer):
            raise TypeError("server must be AdbServer")

    def _stale_server_error(self, server: AdbServer) -> AdbServerStaleOwnerError:
        lifetime = self._active_lifetime
        current = lifetime.owner.server if lifetime is not None else None
        return AdbServerStaleOwnerError(
            f"ADB server {server!r} is stale; current server is {current!r}"
        )


# Private compatibility alias: the implementation is a lifetime store, not a process singleton.
_ProcessAdbServerOwner = _OwnedAdbServerLifetimeStore


def acquire_process_adb_server(endpoint: AdbServerEndpoint | None = None) -> AdbServer:
    """Acquire or create the process-coordinated ADB server lifetime."""

    from adb.server.coordination import _PROCESS_ADB_SERVER_COORDINATOR

    return _PROCESS_ADB_SERVER_COORDINATOR.acquire_server(endpoint)


def invalidate_process_adb_server(server: AdbServer) -> bool:
    """Retire and dispose one server after terminal liveness loss."""

    from adb.server.coordination import _PROCESS_ADB_SERVER_COORDINATOR

    return _PROCESS_ADB_SERVER_COORDINATOR.invalidate_server(server)


def close_process_adb_server(server: AdbServer) -> None:
    """Retire and close one exact server through its private native handle."""

    from adb.server.coordination import _PROCESS_ADB_SERVER_COORDINATOR

    _PROCESS_ADB_SERVER_COORDINATOR.close_server(server)


__all__ = [
    "AdbServerOwnershipLostError",
    "AdbServerStaleOwnerError",
    "acquire_process_adb_server",
    "close_process_adb_server",
    "invalidate_process_adb_server",
]
