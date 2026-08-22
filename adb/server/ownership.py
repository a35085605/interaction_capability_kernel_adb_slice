from __future__ import annotations

from enum import Enum
from threading import Condition

from adb.server.identity import (
    AdbServerIncarnation,
    AdbServerIncarnationId,
    _AdbServerIncarnationSequence,
)
from adb.server.model import AdbServerEndpoint
from adb.server.lifecycle.handle import AdbServerNativeHandle
from adb.server.lifecycle.launch import AdbServerLauncher


_OWNED_SERVER_CONSTRUCTION_TOKEN = object()


class AdbServerOwnershipLostError(RuntimeError):
    """The process has no currently usable owned ADB server lifetime."""


class AdbServerStaleOwnerError(AdbServerOwnershipLostError):
    """An ownership operation referenced an ADB server incarnation that is no longer current."""


class AdbOwnedServer:
    """Owned relationship to one exact ADB server incarnation.

    Ownership means this process retains private exact-lifetime teardown authority. Incarnation
    identity is a separate value and is exposed so consumers can fence delayed work without
    treating generation itself as an ownership concept.
    """

    __slots__ = ("_incarnation",)

    def __init__(
        self,
        incarnation: AdbServerIncarnation,
        *,
        _token: object,
    ) -> None:
        if _token is not _OWNED_SERVER_CONSTRUCTION_TOKEN:
            raise TypeError("AdbOwnedServer values are created by the owned lifetime store")
        if not isinstance(incarnation, AdbServerIncarnation):
            raise TypeError("incarnation must be AdbServerIncarnation")
        self._incarnation = incarnation

    @classmethod
    def _from_incarnation(cls, incarnation: AdbServerIncarnation) -> "AdbOwnedServer":
        return cls(incarnation, _token=_OWNED_SERVER_CONSTRUCTION_TOKEN)

    @classmethod
    def _from_identity(
        cls,
        endpoint: AdbServerEndpoint,
        generation: int,
    ) -> "AdbOwnedServer":
        """Compatibility constructor for internal callers migrating to incarnation identity."""

        return cls._from_incarnation(
            AdbServerIncarnation(endpoint, AdbServerIncarnationId(generation))
        )

    @property
    def incarnation(self) -> AdbServerIncarnation:
        return self._incarnation

    @property
    def endpoint(self) -> AdbServerEndpoint:
        """Compatibility projection of :attr:`incarnation`."""

        return self._incarnation.endpoint

    @property
    def generation(self) -> int:
        """Compatibility projection of the incarnation fencing generation."""

        return self._incarnation.generation



class _OwnedServerLifetime:
    """Private exact-lifetime authority backing one owned relationship."""

    __slots__ = ("owner", "native")

    def __init__(self, owner: AdbOwnedServer, native: AdbServerNativeHandle) -> None:
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
    """Private teardown state for one irreversibly retired incarnation."""

    __slots__ = ("lifetime", "status", "close_failure")

    def __init__(self, lifetime: _OwnedServerLifetime) -> None:
        self.lifetime = lifetime
        self.status = _RetiredServerLifetimeStatus.CLOSING
        self.close_failure: BaseException | None = None


class _OwnedAdbServerLifetimeStore:
    """Serialize exact owned lifetimes without defining process mutation authority.

    This primitive owns native handles and retirement bookkeeping. Process singleton scope,
    exclusive mutation leases, and supervision policy live above it in ``adb.server.coordination``.
    """

    def __init__(
        self,
        launcher: AdbServerLauncher | None = None,
        *,
        incarnation_sequence: _AdbServerIncarnationSequence | None = None,
    ) -> None:
        if launcher is None:
            launcher = _DefaultAdbServerLauncher()
        elif not isinstance(launcher, AdbServerLauncher):
            raise TypeError("launcher must satisfy AdbServerLauncher")
        if incarnation_sequence is None:
            incarnation_sequence = _AdbServerIncarnationSequence()
        elif not isinstance(incarnation_sequence, _AdbServerIncarnationSequence):
            raise TypeError("incarnation_sequence must be _AdbServerIncarnationSequence")
        self._launcher = launcher
        self._incarnations = incarnation_sequence
        self._condition = Condition()
        self._status = _OwnedAdbServerStoreStatus.ABSENT
        self._active_lifetime: _OwnedServerLifetime | None = None
        self._retired_lifetimes: dict[AdbServerIncarnationId, _RetiredServerLifetime] = {}

    def acquire(self, endpoint: AdbServerEndpoint | None = None) -> AdbOwnedServer:
        """Return the active owned relationship or launch one fresh exact native lifetime."""

        if endpoint is not None and not isinstance(endpoint, AdbServerEndpoint):
            raise TypeError("endpoint must be AdbServerEndpoint or None")

        with self._condition:
            while self._status is _OwnedAdbServerStoreStatus.STARTING:
                self._condition.wait()

            if self._status is _OwnedAdbServerStoreStatus.ACTIVE:
                assert self._active_lifetime is not None
                return self._active_lifetime.owner

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

        with self._condition:
            incarnation = self._incarnations.next(native.endpoint)
            owner = AdbOwnedServer._from_incarnation(incarnation)
            self._active_lifetime = _OwnedServerLifetime(owner, native)
            self._status = _OwnedAdbServerStoreStatus.ACTIVE
            self._condition.notify_all()
            return owner

    def retire(self, owner: AdbOwnedServer) -> bool:
        """Irreversibly withdraw one owned incarnation from the active projection."""

        self._require_owner(owner)
        with self._condition:
            retired = self._retired_lifetimes.get(owner.incarnation.id)
            if retired is not None and retired.lifetime.owner is owner:
                return False

            lifetime = self._active_lifetime
            if lifetime is None:
                latest_id = self._incarnations.latest_id
                if latest_id is not None and owner.incarnation.id <= latest_id:
                    return False
                raise self._stale_owner_error(owner)
            if lifetime.owner is not owner:
                if owner.incarnation.id < lifetime.owner.incarnation.id:
                    return False
                raise self._stale_owner_error(owner)
            if self._status is not _OwnedAdbServerStoreStatus.ACTIVE:
                raise self._stale_owner_error(owner)

            self._active_lifetime = None
            self._retired_lifetimes[owner.incarnation.id] = _RetiredServerLifetime(lifetime)
            self._status = _OwnedAdbServerStoreStatus.ABSENT
            self._condition.notify_all()
            return True

    def dispose_retired(self, owner: AdbOwnedServer) -> None:
        """Prove native termination for one already-retired owned incarnation."""

        self._require_owner(owner)
        with self._condition:
            retired = self._retired_lifetimes.get(owner.incarnation.id)
            if retired is None or retired.lifetime.owner is not owner:
                raise self._stale_owner_error(owner)
            retired.status = _RetiredServerLifetimeStatus.CLOSING
            retired.close_failure = None
            native = retired.lifetime.native

        try:
            native.close()
        except BaseException as exc:
            with self._condition:
                current = self._retired_lifetimes.get(owner.incarnation.id)
                if current is retired:
                    retired.status = _RetiredServerLifetimeStatus.CLOSE_UNPROVEN
                    retired.close_failure = exc
                    self._condition.notify_all()
            raise

        with self._condition:
            current = self._retired_lifetimes.get(owner.incarnation.id)
            if current is retired:
                del self._retired_lifetimes[owner.incarnation.id]
                self._condition.notify_all()

    def invalidate(self, owner: AdbOwnedServer) -> bool:
        """Retire and synchronously dispose one owned incarnation after liveness loss."""

        retired_now = self.retire(owner)
        with self._condition:
            retired = self._retired_lifetimes.get(owner.incarnation.id)
            can_dispose = retired is not None and retired.lifetime.owner is owner
        if not can_dispose:
            return False
        self.dispose_retired(owner)
        return retired_now or can_dispose

    def close(self, owner: AdbOwnedServer) -> None:
        """Retire and synchronously close one exact owned incarnation."""

        retired_now = self.retire(owner)
        if not retired_now:
            with self._condition:
                retired = self._retired_lifetimes.get(owner.incarnation.id)
                if retired is None or retired.lifetime.owner is not owner:
                    raise self._stale_owner_error(owner)
        self.dispose_retired(owner)

    @property
    def active_server(self) -> AdbOwnedServer | None:
        """Return the active owned relationship without launching a new lifetime."""

        with self._condition:
            if self._status is not _OwnedAdbServerStoreStatus.ACTIVE:
                return None
            assert self._active_lifetime is not None
            return self._active_lifetime.owner

    @property
    def active_owner(self) -> AdbOwnedServer | None:
        """Compatibility alias for :attr:`active_server`."""

        return self.active_server

    @staticmethod
    def _require_owner(owner: object) -> None:
        if not isinstance(owner, AdbOwnedServer):
            raise TypeError("owner must be AdbOwnedServer")

    def _stale_owner_error(self, owner: AdbOwnedServer) -> AdbServerStaleOwnerError:
        lifetime = self._active_lifetime
        current = lifetime.owner.incarnation if lifetime is not None else None
        return AdbServerStaleOwnerError(
            f"ADB server incarnation {owner.incarnation!r} is stale; current incarnation is {current!r}"
        )


# Private compatibility alias: the implementation is a lifetime store, not a process singleton.
_ProcessAdbServerOwner = _OwnedAdbServerLifetimeStore


def acquire_process_adb_server(
    endpoint: AdbServerEndpoint | None = None,
) -> AdbOwnedServer:
    """Acquire or create the process-coordinated owned ADB server lifetime."""

    from adb.server.coordination import _PROCESS_ADB_SERVER_COORDINATOR

    return _PROCESS_ADB_SERVER_COORDINATOR.acquire_owned(endpoint)


def invalidate_process_adb_server(owner: AdbOwnedServer) -> bool:
    """Retire and dispose one owned incarnation after terminal liveness loss."""

    from adb.server.coordination import _PROCESS_ADB_SERVER_COORDINATOR

    return _PROCESS_ADB_SERVER_COORDINATOR.invalidate_owned(owner)


def close_process_adb_server(owner: AdbOwnedServer) -> None:
    """Retire and close one exact owned incarnation through its private native handle."""

    from adb.server.coordination import _PROCESS_ADB_SERVER_COORDINATOR

    _PROCESS_ADB_SERVER_COORDINATOR.close_owned(owner)


__all__ = [
    "AdbOwnedServer",
    "AdbServerOwnershipLostError",
    "AdbServerStaleOwnerError",
    "acquire_process_adb_server",
    "close_process_adb_server",
    "invalidate_process_adb_server",
]
