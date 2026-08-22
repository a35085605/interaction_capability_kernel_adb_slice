from __future__ import annotations

from collections.abc import Callable, Iterable
from enum import Enum
from threading import Condition

from adb.server.endpoint import AdbServerEndpoint
from adb.server.identity import AdbServer
from adb.server.lifecycle.backend import (
    AdbServerLifecycleBackend,
    LauncherAdbServerLifecycleBackend,
)
from adb.server.lifecycle.launch import AdbServerLauncher


class AdbServerOwnership(str, Enum):
    """ADB-level creation provenance and lifecycle responsibility.

    This value deliberately says nothing about OS/process handles or termination capability.
    ``OWNED`` means this coordination domain created the server and accepts lifecycle
    responsibility for it. ``ADOPTED`` and ``UNKNOWN`` remain valid relationships even though a
    raw :class:`~adb.server.control.AdbServerController` may still be technically capable of
    issuing ``adb kill-server`` for them.
    """

    OWNED = "owned"
    ADOPTED = "adopted"
    UNKNOWN = "unknown"


class AdbServerTerminationPolicy:
    """Policy deciding which ADB ownership relationships may request termination.

    Capability and authorization are intentionally separate: this policy does not execute a
    termination mechanism and an :class:`~adb.server.control.AdbServerController` does not consult
    it implicitly.
    """

    __slots__ = ("_allowed",)

    def __init__(self, allowed: Iterable[AdbServerOwnership]) -> None:
        normalized = frozenset(allowed)
        if any(not isinstance(value, AdbServerOwnership) for value in normalized):
            raise TypeError("allowed values must be AdbServerOwnership")
        self._allowed = normalized

    @property
    def allowed(self) -> frozenset[AdbServerOwnership]:
        return self._allowed

    def allows(self, ownership: AdbServerOwnership) -> bool:
        if not isinstance(ownership, AdbServerOwnership):
            raise TypeError("ownership must be AdbServerOwnership")
        return ownership in self._allowed


OWNED_ONLY_ADB_SERVER_TERMINATION_POLICY = AdbServerTerminationPolicy(
    (AdbServerOwnership.OWNED,)
)
ANY_ADB_SERVER_TERMINATION_POLICY = AdbServerTerminationPolicy(tuple(AdbServerOwnership))


class AdbServerOwnershipLostError(RuntimeError):
    """The coordination domain has no currently usable ADB-owned server lifetime."""


class AdbServerStaleOwnerError(AdbServerOwnershipLostError):
    """An ADB ownership operation referenced a server that is no longer current."""


class _AdbServerRecord:
    """Private ADB-domain record containing identity and creation provenance only."""

    __slots__ = ("server", "ownership")

    def __init__(self, server: AdbServer, ownership: AdbServerOwnership) -> None:
        if not isinstance(server, AdbServer):
            raise TypeError("server must be AdbServer")
        if not isinstance(ownership, AdbServerOwnership):
            raise TypeError("ownership must be AdbServerOwnership")
        self.server = server
        self.ownership = ownership


class _DefaultAdbServerLauncher:
    """Compatibility launcher used to build the default lifecycle backend lazily."""

    def __init__(self) -> None:
        self._delegate: AdbServerLauncher | None = None

    def launch(self, endpoint: AdbServerEndpoint | None = None):
        delegate = self._delegate
        if delegate is None:
            from adb.server.lifecycle.subprocess import SubprocessAdbServerLauncher

            delegate = SubprocessAdbServerLauncher()
            self._delegate = delegate
        return delegate.launch(endpoint)


class _AdbServerStoreStatus(str, Enum):
    ABSENT = "absent"
    STARTING = "starting"
    ACTIVE = "active"


class _RetiredServerLifetimeStatus(str, Enum):
    CLOSING = "closing"
    CLOSE_UNPROVEN = "close_unproven"


class _RetiredServerRecord:
    """Private teardown state for one irreversibly retired ADB server record."""

    __slots__ = ("record", "status", "close_failure")

    def __init__(self, record: _AdbServerRecord) -> None:
        self.record = record
        self.status = _RetiredServerLifetimeStatus.CLOSING
        self.close_failure: BaseException | None = None


class _AdbServerLifetimeStore:
    """Serialize ADB-owned server identities without retaining OS/process handles.

    The store owns only ADB-domain state: server identity, creation provenance, and retirement
    state. Exact process lifetime capabilities are retained behind ``AdbServerLifecycleBackend``.
    Process singleton scope, exclusive mutation leases, epoch generation, and supervision policy
    live above this store in ``adb.server.coordination``.

    ``launcher`` remains accepted as a compatibility construction path; it is immediately wrapped
    by ``LauncherAdbServerLifecycleBackend`` so its process-lifetime capability never enters this
    store.
    """

    def __init__(
        self,
        launcher: AdbServerLauncher | None = None,
        *,
        backend: AdbServerLifecycleBackend | None = None,
    ) -> None:
        if launcher is not None and backend is not None:
            raise TypeError("specify launcher or backend, not both")
        if backend is None:
            if launcher is None:
                launcher = _DefaultAdbServerLauncher()
            elif not isinstance(launcher, AdbServerLauncher):
                raise TypeError("launcher must satisfy AdbServerLauncher")
            backend = LauncherAdbServerLifecycleBackend(launcher)
        elif not isinstance(backend, AdbServerLifecycleBackend):
            raise TypeError("backend must satisfy AdbServerLifecycleBackend")

        self._backend = backend
        self._condition = Condition()
        self._status = _AdbServerStoreStatus.ABSENT
        self._active_record: _AdbServerRecord | None = None
        self._retired_records: dict[AdbServer, _RetiredServerRecord] = {}

    def acquire(
        self,
        endpoint: AdbServerEndpoint | None = None,
        *,
        server_factory: Callable[[AdbServerEndpoint], AdbServer],
    ) -> AdbServer:
        """Return the active server or create one fresh ADB-owned server."""

        if endpoint is not None and not isinstance(endpoint, AdbServerEndpoint):
            raise TypeError("endpoint must be AdbServerEndpoint or None")
        if not callable(server_factory):
            raise TypeError("server_factory must be callable")

        with self._condition:
            while self._status is _AdbServerStoreStatus.STARTING:
                self._condition.wait()

            if self._status is _AdbServerStoreStatus.ACTIVE:
                assert self._active_record is not None
                return self._active_record.server

            assert self._status is _AdbServerStoreStatus.ABSENT
            self._status = _AdbServerStoreStatus.STARTING

        try:
            server = self._backend.create(endpoint, server_factory=server_factory)
            if not isinstance(server, AdbServer):
                raise TypeError("backend.create() must return AdbServer")
            record = _AdbServerRecord(server, AdbServerOwnership.OWNED)
        except BaseException:
            with self._condition:
                self._status = _AdbServerStoreStatus.ABSENT
                self._active_record = None
                self._condition.notify_all()
            raise

        with self._condition:
            self._active_record = record
            self._status = _AdbServerStoreStatus.ACTIVE
            self._condition.notify_all()
            return server

    def retire(self, server: AdbServer) -> bool:
        """Irreversibly withdraw one server from the active ADB-domain projection."""

        self._require_server(server)
        with self._condition:
            if server in self._retired_records:
                return False

            record = self._active_record
            if record is None:
                return False
            if record.server != server:
                return False
            if self._status is not _AdbServerStoreStatus.ACTIVE:
                raise self._stale_server_error(server)

            self._active_record = None
            self._retired_records[server] = _RetiredServerRecord(record)
            self._status = _AdbServerStoreStatus.ABSENT
            self._condition.notify_all()
            return True

    def dispose_retired(self, server: AdbServer) -> None:
        """Ask the lifecycle backend to terminate one already-retired created server."""

        self._require_server(server)
        with self._condition:
            retired = self._retired_records.get(server)
            if retired is None:
                raise self._stale_server_error(server)
            retired.status = _RetiredServerLifetimeStatus.CLOSING
            retired.close_failure = None

        try:
            self._backend.close(server)
        except BaseException as exc:
            with self._condition:
                current = self._retired_records.get(server)
                if current is retired:
                    retired.status = _RetiredServerLifetimeStatus.CLOSE_UNPROVEN
                    retired.close_failure = exc
                    self._condition.notify_all()
            raise

        with self._condition:
            current = self._retired_records.get(server)
            if current is retired:
                del self._retired_records[server]
                self._condition.notify_all()

    def invalidate(self, server: AdbServer) -> bool:
        """Retire and synchronously dispose one created server after liveness loss."""

        retired_now = self.retire(server)
        with self._condition:
            can_dispose = server in self._retired_records
        if not can_dispose:
            return False
        self.dispose_retired(server)
        return retired_now or can_dispose

    def close(self, server: AdbServer) -> None:
        """Retire and synchronously close one process-coordinated created server."""

        retired_now = self.retire(server)
        if not retired_now:
            with self._condition:
                if server not in self._retired_records:
                    raise self._stale_server_error(server)
        self.dispose_retired(server)

    @property
    def active_server(self) -> AdbServer | None:
        """Return the active server identity without creating a new lifetime."""

        with self._condition:
            if self._status is not _AdbServerStoreStatus.ACTIVE:
                return None
            assert self._active_record is not None
            return self._active_record.server

    @property
    def active_ownership(self) -> AdbServerOwnership | None:
        """Return the ADB-level ownership relationship for the active server."""

        with self._condition:
            if self._status is not _AdbServerStoreStatus.ACTIVE:
                return None
            assert self._active_record is not None
            return self._active_record.ownership

    def ownership_of(self, server: AdbServer) -> AdbServerOwnership:
        """Return ADB-level ownership for an active or not-yet-disposed retired server."""

        self._require_server(server)
        with self._condition:
            active = self._active_record
            if active is not None and active.server == server:
                return active.ownership
            retired = self._retired_records.get(server)
            if retired is not None:
                return retired.record.ownership
            raise self._stale_server_error(server)

    @staticmethod
    def _require_server(server: object) -> None:
        if not isinstance(server, AdbServer):
            raise TypeError("server must be AdbServer")

    def _stale_server_error(self, server: AdbServer) -> AdbServerStaleOwnerError:
        record = self._active_record
        current = record.server if record is not None else None
        return AdbServerStaleOwnerError(
            f"ADB server {server!r} is stale; current server is {current!r}"
        )


# Compatibility names for private callers while the implementation moves from process ownership
# to ADB-domain provenance plus a backend-owned process lifetime.
_OwnedAdbServerLifetimeStore = _AdbServerLifetimeStore
_ProcessAdbServerOwner = _AdbServerLifetimeStore


def acquire_process_adb_server(endpoint: AdbServerEndpoint | None = None) -> AdbServer:
    """Acquire or create the process-coordinated ADB-owned server lifetime."""

    from adb.server.coordination import _PROCESS_ADB_SERVER_COORDINATOR

    return _PROCESS_ADB_SERVER_COORDINATOR.acquire_server(endpoint)


def invalidate_process_adb_server(server: AdbServer) -> bool:
    """Retire and dispose one ADB-owned server after terminal liveness loss."""

    from adb.server.coordination import _PROCESS_ADB_SERVER_COORDINATOR

    return _PROCESS_ADB_SERVER_COORDINATOR.invalidate_server(server)


def close_process_adb_server(server: AdbServer) -> None:
    """Retire and close one ADB-owned server through its lifecycle backend."""

    from adb.server.coordination import _PROCESS_ADB_SERVER_COORDINATOR

    _PROCESS_ADB_SERVER_COORDINATOR.close_server(server)


__all__ = [
    "ANY_ADB_SERVER_TERMINATION_POLICY",
    "AdbServerOwnership",
    "AdbServerOwnershipLostError",
    "AdbServerStaleOwnerError",
    "AdbServerTerminationPolicy",
    "OWNED_ONLY_ADB_SERVER_TERMINATION_POLICY",
    "acquire_process_adb_server",
    "close_process_adb_server",
    "invalidate_process_adb_server",
]
