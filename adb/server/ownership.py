from __future__ import annotations

from enum import Enum
from threading import Condition

from adb.server.model import AdbServerEndpoint
from adb.server.lifecycle.handle import AdbServerNativeHandle
from adb.server.lifecycle.launch import AdbServerLauncher


_OWNER_CONSTRUCTION_TOKEN = object()
_SUPERVISION_LEASE_CONSTRUCTION_TOKEN = object()


class AdbServerOwnershipLostError(RuntimeError):
    """The process has no currently usable owned ADB server lifetime."""


class AdbServerStaleOwnerError(AdbServerOwnershipLostError):
    """An operation referenced an ADB server generation that is no longer current."""


class AdbOwnedServer:
    """Public identity of one exact process-owned ADB server generation.

    The native lifecycle handle is deliberately private to the process owner. Public consumers
    only receive the immutable endpoint and monotonic process-local generation identifying the
    currently usable ownership lifetime. Once that generation is retired it can never become
    usable again.
    """

    __slots__ = ("_endpoint", "_generation")

    def __init__(
        self,
        endpoint: AdbServerEndpoint,
        generation: int,
        *,
        _token: object,
    ) -> None:
        if _token is not _OWNER_CONSTRUCTION_TOKEN:
            raise TypeError("AdbOwnedServer values are created by the process ADB server owner")
        if not isinstance(endpoint, AdbServerEndpoint):
            raise TypeError("endpoint must be AdbServerEndpoint")
        if isinstance(generation, bool) or not isinstance(generation, int):
            raise TypeError("generation must be an integer")
        if generation <= 0:
            raise ValueError("generation must be greater than zero")
        self._endpoint = endpoint
        self._generation = generation

    @classmethod
    def _from_identity(
        cls,
        endpoint: AdbServerEndpoint,
        generation: int,
    ) -> "AdbOwnedServer":
        return cls(endpoint, generation, _token=_OWNER_CONSTRUCTION_TOKEN)

    @property
    def endpoint(self) -> AdbServerEndpoint:
        return self._endpoint

    @property
    def generation(self) -> int:
        """Process-local identity of this exact owned server lifetime."""

        return self._generation


class _OwnedServerLifetime:
    """Private native authority backing one public owned-server generation."""

    __slots__ = ("owner", "native")

    def __init__(self, owner: AdbOwnedServer, native: AdbServerNativeHandle) -> None:
        self.owner = owner
        self.native = native


class _AdbServerSupervisionLease:
    """Opaque authority proving exclusive supervision of one process owner."""

    __slots__ = ()

    def __init__(self, *, _token: object) -> None:
        if _token is not _SUPERVISION_LEASE_CONSTRUCTION_TOKEN:
            raise TypeError(
                "ADB server supervision leases are created by the process ADB server owner"
            )

    @classmethod
    def _new(cls) -> "_AdbServerSupervisionLease":
        return cls(_token=_SUPERVISION_LEASE_CONSTRUCTION_TOKEN)


class _DefaultAdbServerLauncher:
    """Lazily construct the concrete launcher after the ADB package graph is imported."""

    def __init__(self) -> None:
        self._delegate: AdbServerLauncher | None = None

    @property
    def requires_retired_close_before_launch(self) -> bool:
        return True

    def launch(self) -> AdbServerNativeHandle:
        delegate = self._delegate
        if delegate is None:
            from adb.server.lifecycle.subprocess import SubprocessAdbServerLauncher

            delegate = SubprocessAdbServerLauncher()
            self._delegate = delegate
        return delegate.launch()


class _ProcessAdbServerOwnerStatus(str, Enum):
    ABSENT = "absent"
    STARTING = "starting"
    ACTIVE = "active"


class _RetiredServerLifetimeStatus(str, Enum):
    CLOSING = "closing"
    CLOSE_UNPROVEN = "close_unproven"


class _RetiredServerLifetime:
    """Private teardown state for one irreversibly retired generation."""

    __slots__ = ("lifetime", "status", "close_failure")

    def __init__(self, lifetime: _OwnedServerLifetime) -> None:
        self.lifetime = lifetime
        self.status = _RetiredServerLifetimeStatus.CLOSING
        self.close_failure: BaseException | None = None


class _ProcessAdbServerOwner:
    """Serialize public ownership and private native ADB server lifetimes.

    Public ownership and native teardown are deliberately separate. Retiring a generation
    irreversibly removes it from the public projection and moves its exact native handle into
    retired-lifetime tracking. Whether a fresh generation must wait for all retired native
    lifetimes to be proven closed is a launcher/owner policy rather than an ownership invariant.
    """

    def __init__(
        self,
        launcher: AdbServerLauncher | None = None,
        *,
        require_retired_close_before_launch: bool | None = None,
    ) -> None:
        if launcher is None:
            launcher = _DefaultAdbServerLauncher()
        elif not isinstance(launcher, AdbServerLauncher):
            raise TypeError("launcher must satisfy AdbServerLauncher")
        if require_retired_close_before_launch is None:
            require_retired_close_before_launch = getattr(
                launcher,
                "requires_retired_close_before_launch",
                True,
            )
        if not isinstance(require_retired_close_before_launch, bool):
            raise TypeError("require_retired_close_before_launch must be a bool")
        self._launcher = launcher
        self._condition = Condition()
        self._status = _ProcessAdbServerOwnerStatus.ABSENT
        self._active_lifetime: _OwnedServerLifetime | None = None
        self._retired_lifetimes: dict[int, _RetiredServerLifetime] = {}
        self._generation = 0
        self._require_retired_close_before_launch = require_retired_close_before_launch
        self._supervision_lease: _AdbServerSupervisionLease | None = None

    @property
    def requires_retired_close_before_launch(self) -> bool:
        """Whether a fresh generation is fenced behind retired native close proof."""

        return self._require_retired_close_before_launch

    def claim_supervision(self, owner: AdbOwnedServer) -> _AdbServerSupervisionLease:
        """Exclusively claim lifecycle supervision authority across server generations.

        The claim is bound to this process owner rather than to one generation so a supervisor
        can retain durable recovery intent while the active owner is temporarily absent. The
        initial generation check and claim are serialized as one operation.
        """

        self._require_owner(owner)
        with self._condition:
            lifetime = self._active_lifetime
            if (
                self._status is not _ProcessAdbServerOwnerStatus.ACTIVE
                or lifetime is None
                or lifetime.owner is not owner
            ):
                raise ValueError("server must be the process owner's active generation")
            if self._supervision_lease is not None:
                raise RuntimeError("ADB server supervision authority is already claimed")
            lease = _AdbServerSupervisionLease._new()
            self._supervision_lease = lease
            return lease

    def release_supervision(self, lease: _AdbServerSupervisionLease) -> None:
        """Release one exact supervision claim without changing native server ownership."""

        if not isinstance(lease, _AdbServerSupervisionLease):
            raise TypeError("lease must be _AdbServerSupervisionLease")
        with self._condition:
            if self._supervision_lease is not lease:
                raise RuntimeError("ADB server supervision lease is not active")
            self._supervision_lease = None

    def acquire(self) -> AdbOwnedServer:
        """Return the active generation or launch one fresh process-owned server.

        Retired native lifetimes remain independently tracked until close is proven. Owners that
        require endpoint/native exclusivity fence fresh launch behind that proof; non-pinned owners
        may launch a new generation immediately after retirement.
        """

        with self._condition:
            while self._status is _ProcessAdbServerOwnerStatus.STARTING:
                self._condition.wait()

            if self._status is _ProcessAdbServerOwnerStatus.ACTIVE:
                assert self._active_lifetime is not None
                return self._active_lifetime.owner

            if self._require_retired_close_before_launch:
                while any(
                    retired.status is _RetiredServerLifetimeStatus.CLOSING
                    for retired in self._retired_lifetimes.values()
                ):
                    self._condition.wait()
                unproven = next(
                    (
                        retired
                        for retired in self._retired_lifetimes.values()
                        if retired.status is _RetiredServerLifetimeStatus.CLOSE_UNPROVEN
                    ),
                    None,
                )
                if unproven is not None:
                    error = AdbServerOwnershipLostError(
                        "cannot acquire a new ADB server generation while termination of a "
                        "previous owned lifetime remains unproven"
                    )
                    if unproven.close_failure is not None:
                        raise error from unproven.close_failure
                    raise error

            assert self._status is _ProcessAdbServerOwnerStatus.ABSENT
            self._status = _ProcessAdbServerOwnerStatus.STARTING

        try:
            native = self._launcher.launch()
            if not isinstance(native, AdbServerNativeHandle):
                raise TypeError("launcher.launch() must return AdbServerNativeHandle")
        except BaseException:
            with self._condition:
                self._status = _ProcessAdbServerOwnerStatus.ABSENT
                self._active_lifetime = None
                self._condition.notify_all()
            raise

        with self._condition:
            self._generation += 1
            owner = AdbOwnedServer._from_identity(native.endpoint, self._generation)
            self._active_lifetime = _OwnedServerLifetime(owner, native)
            self._status = _ProcessAdbServerOwnerStatus.ACTIVE
            self._condition.notify_all()
            return owner

    def retire(self, owner: AdbOwnedServer) -> bool:
        """Irreversibly withdraw one generation from public ownership.

        Retirement is non-blocking with respect to native process termination. The exact native
        handle is retained in private retired-lifetime tracking until :meth:`dispose_retired`
        proves termination.
        """

        self._require_owner(owner)
        with self._condition:
            retired = self._retired_lifetimes.get(owner.generation)
            if retired is not None and retired.lifetime.owner is owner:
                return False

            lifetime = self._active_lifetime
            if lifetime is None:
                if owner.generation <= self._generation:
                    return False
                raise self._stale_owner_error(owner)
            if lifetime.owner is not owner:
                if owner.generation < lifetime.owner.generation:
                    return False
                raise self._stale_owner_error(owner)
            if self._status is not _ProcessAdbServerOwnerStatus.ACTIVE:
                raise self._stale_owner_error(owner)

            self._active_lifetime = None
            self._retired_lifetimes[owner.generation] = _RetiredServerLifetime(lifetime)
            self._status = _ProcessAdbServerOwnerStatus.ABSENT
            self._condition.notify_all()
            return True

    def dispose_retired(self, owner: AdbOwnedServer) -> None:
        """Prove native termination for one already-retired generation.

        Failure leaves only that retired native lifetime as ``CLOSE_UNPROVEN``. A later call for
        the same generation may retry close proof without affecting any newer active generation.
        """

        self._require_owner(owner)
        with self._condition:
            retired = self._retired_lifetimes.get(owner.generation)
            if retired is None or retired.lifetime.owner is not owner:
                raise self._stale_owner_error(owner)
            retired.status = _RetiredServerLifetimeStatus.CLOSING
            retired.close_failure = None
            native = retired.lifetime.native

        try:
            native.close()
        except BaseException as exc:
            with self._condition:
                current = self._retired_lifetimes.get(owner.generation)
                if current is retired:
                    retired.status = _RetiredServerLifetimeStatus.CLOSE_UNPROVEN
                    retired.close_failure = exc
                    self._condition.notify_all()
            raise

        with self._condition:
            current = self._retired_lifetimes.get(owner.generation)
            if current is retired:
                del self._retired_lifetimes[owner.generation]
                self._condition.notify_all()

    def invalidate(self, owner: AdbOwnedServer) -> bool:
        """Retire and synchronously dispose one generation after liveness loss."""

        retired_now = self.retire(owner)
        with self._condition:
            retired = self._retired_lifetimes.get(owner.generation)
            can_dispose = retired is not None and retired.lifetime.owner is owner
        if not can_dispose:
            return False
        self.dispose_retired(owner)
        return retired_now or can_dispose

    def close(self, owner: AdbOwnedServer) -> None:
        """Retire and synchronously close one exact owned generation."""

        retired_now = self.retire(owner)
        if not retired_now:
            with self._condition:
                retired = self._retired_lifetimes.get(owner.generation)
                if retired is None or retired.lifetime.owner is not owner:
                    raise self._stale_owner_error(owner)
        self.dispose_retired(owner)

    @property
    def active_owner(self) -> AdbOwnedServer | None:
        """Return the current public ownership projection without launching a generation."""

        with self._condition:
            if self._status is not _ProcessAdbServerOwnerStatus.ACTIVE:
                return None
            assert self._active_lifetime is not None
            return self._active_lifetime.owner

    @staticmethod
    def _require_owner(owner: object) -> None:
        if not isinstance(owner, AdbOwnedServer):
            raise TypeError("owner must be AdbOwnedServer")

    def _stale_owner_error(self, owner: AdbOwnedServer) -> AdbServerStaleOwnerError:
        lifetime = self._active_lifetime
        current_generation = lifetime.owner.generation if lifetime is not None else None
        return AdbServerStaleOwnerError(
            "ADB server generation "
            f"{owner.generation} is stale; current generation is {current_generation!r}"
        )


_PROCESS_ADB_SERVER_OWNER = _ProcessAdbServerOwner()


def acquire_process_adb_server() -> AdbOwnedServer:
    """Acquire or create the single process-owned ADB server generation."""

    return _PROCESS_ADB_SERVER_OWNER.acquire()


def invalidate_process_adb_server(owner: AdbOwnedServer) -> bool:
    """Retire and dispose the current process-owned server after ownership loss."""

    return _PROCESS_ADB_SERVER_OWNER.invalidate(owner)


def close_process_adb_server(owner: AdbOwnedServer) -> None:
    """Retire and close the current process-owned server through its private native handle."""

    _PROCESS_ADB_SERVER_OWNER.close(owner)


__all__ = [
    "AdbOwnedServer",
    "AdbServerOwnershipLostError",
    "AdbServerStaleOwnerError",
    "acquire_process_adb_server",
    "close_process_adb_server",
    "invalidate_process_adb_server",
]
