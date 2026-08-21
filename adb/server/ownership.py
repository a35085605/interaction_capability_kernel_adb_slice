from __future__ import annotations

from enum import Enum
from threading import Condition

from adb.server.model import AdbServerEndpoint
from adb.server.lifecycle.handle import AdbServerLauncher, AdbServerNativeHandle


_OWNER_CONSTRUCTION_TOKEN = object()


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


class _DefaultAdbServerLauncher:
    """Lazily construct the concrete launcher after the ADB package graph is imported."""

    def __init__(self) -> None:
        self._delegate: AdbServerLauncher | None = None

    def launch(self) -> AdbServerNativeHandle:
        delegate = self._delegate
        if delegate is None:
            from adb.server.lifecycle.launch import SubprocessAdbServerLauncher

            delegate = SubprocessAdbServerLauncher()
            self._delegate = delegate
        return delegate.launch()


class _ProcessAdbServerOwnerStatus(str, Enum):
    ABSENT = "absent"
    STARTING = "starting"
    ACTIVE = "active"
    CLOSING = "closing"
    CLOSE_UNPROVEN = "close_unproven"


class _ProcessAdbServerOwner:
    """Serialize public ownership and private native ADB server lifetimes.

    Public ownership has only two projections: an :class:`AdbOwnedServer` is currently usable,
    or no owned server is available. Native teardown is a separate private lifecycle. Retiring
    a generation removes it from the public projection before potentially blocking native close
    work begins. Close failure never resurrects the retired generation.
    """

    def __init__(self, launcher: AdbServerLauncher | None = None) -> None:
        if launcher is None:
            launcher = _DefaultAdbServerLauncher()
        if not isinstance(launcher, AdbServerLauncher):
            raise TypeError("launcher must satisfy AdbServerLauncher")
        self._launcher = launcher
        self._condition = Condition()
        self._status = _ProcessAdbServerOwnerStatus.ABSENT
        self._lifetime: _OwnedServerLifetime | None = None
        self._generation = 0
        self._close_failure: BaseException | None = None

    def acquire(self) -> AdbOwnedServer:
        """Return the active generation or launch one fresh process-owned server.

        A generation whose native close could not be proven remains quarantined. The process
        owner will not create a new generation until teardown of the previous native lifetime is
        proven by a later successful ``dispose_retired`` call.
        """

        with self._condition:
            while self._status in {
                _ProcessAdbServerOwnerStatus.STARTING,
                _ProcessAdbServerOwnerStatus.CLOSING,
            }:
                self._condition.wait()

            if self._status is _ProcessAdbServerOwnerStatus.ACTIVE:
                assert self._lifetime is not None
                return self._lifetime.owner
            if self._status is _ProcessAdbServerOwnerStatus.CLOSE_UNPROVEN:
                failure = self._close_failure
                error = AdbServerOwnershipLostError(
                    "cannot acquire a new ADB server generation while termination of the "
                    "previous owned lifetime remains unproven"
                )
                if failure is not None:
                    raise error from failure
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
                self._lifetime = None
                self._condition.notify_all()
            raise

        with self._condition:
            self._generation += 1
            owner = AdbOwnedServer._from_identity(native.endpoint, self._generation)
            self._lifetime = _OwnedServerLifetime(owner, native)
            self._close_failure = None
            self._status = _ProcessAdbServerOwnerStatus.ACTIVE
            self._condition.notify_all()
            return owner

    def retire(self, owner: AdbOwnedServer) -> bool:
        """Irreversibly withdraw one generation from public ownership.

        Retirement is intentionally non-blocking with respect to native process termination. A
        successful return means ``active_owner`` is already ``None`` and callers may immediately
        tear down every server-bound child scope. Native disposal is performed separately through
        :meth:`dispose_retired`.
        """

        self._require_owner(owner)
        with self._condition:
            lifetime = self._lifetime
            if lifetime is None:
                if owner.generation <= self._generation:
                    return False
                raise self._stale_owner_error(owner)
            if lifetime.owner is not owner:
                if owner.generation < lifetime.owner.generation:
                    return False
                raise self._stale_owner_error(owner)
            if self._status is _ProcessAdbServerOwnerStatus.ACTIVE:
                self._status = _ProcessAdbServerOwnerStatus.CLOSING
                self._close_failure = None
                self._condition.notify_all()
                return True
            if self._status in {
                _ProcessAdbServerOwnerStatus.CLOSING,
                _ProcessAdbServerOwnerStatus.CLOSE_UNPROVEN,
            }:
                return False
            raise self._stale_owner_error(owner)

    def dispose_retired(self, owner: AdbOwnedServer) -> None:
        """Prove native termination for one already-retired generation.

        Failure leaves the generation publicly absent and quarantines its private native lifetime
        as ``CLOSE_UNPROVEN``. A later call for the same generation may retry close proof; a new
        generation cannot be acquired until one such attempt succeeds.
        """

        self._require_owner(owner)
        with self._condition:
            lifetime = self._lifetime
            if lifetime is None or lifetime.owner is not owner:
                raise self._stale_owner_error(owner)
            if self._status not in {
                _ProcessAdbServerOwnerStatus.CLOSING,
                _ProcessAdbServerOwnerStatus.CLOSE_UNPROVEN,
            }:
                raise RuntimeError("ADB server generation must be retired before native disposal")
            self._status = _ProcessAdbServerOwnerStatus.CLOSING
            self._close_failure = None
            native = lifetime.native

        try:
            native.close()
        except BaseException as exc:
            with self._condition:
                if self._lifetime is lifetime:
                    self._status = _ProcessAdbServerOwnerStatus.CLOSE_UNPROVEN
                    self._close_failure = exc
                    self._condition.notify_all()
            raise

        with self._condition:
            if self._lifetime is lifetime:
                self._lifetime = None
                self._close_failure = None
                self._status = _ProcessAdbServerOwnerStatus.ABSENT
                self._condition.notify_all()

    def invalidate(self, owner: AdbOwnedServer) -> bool:
        """Retire and synchronously dispose the current generation after liveness loss."""

        retired = self.retire(owner)
        with self._condition:
            lifetime = self._lifetime
            can_dispose = (
                lifetime is not None
                and lifetime.owner is owner
                and self._status
                in {
                    _ProcessAdbServerOwnerStatus.CLOSING,
                    _ProcessAdbServerOwnerStatus.CLOSE_UNPROVEN,
                }
            )
        if not can_dispose:
            return False
        self.dispose_retired(owner)
        return retired or can_dispose

    def close(self, owner: AdbOwnedServer) -> None:
        """Retire and synchronously close the current generation."""

        retired = self.retire(owner)
        if not retired:
            with self._condition:
                lifetime = self._lifetime
                if lifetime is None or lifetime.owner is not owner:
                    raise self._stale_owner_error(owner)
        self.dispose_retired(owner)

    @property
    def active_owner(self) -> AdbOwnedServer | None:
        """Return the current public ownership projection without launching a generation."""

        with self._condition:
            if self._status is not _ProcessAdbServerOwnerStatus.ACTIVE:
                return None
            assert self._lifetime is not None
            return self._lifetime.owner

    @staticmethod
    def _require_owner(owner: object) -> None:
        if not isinstance(owner, AdbOwnedServer):
            raise TypeError("owner must be AdbOwnedServer")

    def _stale_owner_error(self, owner: AdbOwnedServer) -> AdbServerStaleOwnerError:
        lifetime = self._lifetime
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
