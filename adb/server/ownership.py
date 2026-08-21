from __future__ import annotations

from enum import Enum
from threading import Condition

from adb.server.endpoint import AdbServerEndpoint
from adb.server.lifecycle.native import AdbServerLauncher, AdbServerNativeHandle


_OWNER_CONSTRUCTION_TOKEN = object()


class AdbServerOwnershipLostError(RuntimeError):
    """The process has no currently usable owned ADB server lifetime."""


class AdbServerStaleOwnerError(AdbServerOwnershipLostError):
    """An operation referenced an ADB server generation that is no longer current."""


class AdbOwnedServer:
    """One exact native ADB server lifetime created and owned by this process.

    Ownership comes from the native handle returned by :class:`AdbServerLauncher`, never from
    observing an endpoint. Every successful launch receives a new monotonic process-local
    generation. Once fenced, an owner can never become active again.
    """

    __slots__ = ("_native", "_generation", "_active")

    def __init__(
        self,
        native: AdbServerNativeHandle,
        generation: int,
        *,
        _token: object,
    ) -> None:
        if _token is not _OWNER_CONSTRUCTION_TOKEN:
            raise TypeError("AdbOwnedServer values are created by the process ADB server owner")
        if not isinstance(native, AdbServerNativeHandle):
            raise TypeError("native must satisfy AdbServerNativeHandle")
        if isinstance(generation, bool) or not isinstance(generation, int):
            raise TypeError("generation must be an integer")
        if generation <= 0:
            raise ValueError("generation must be greater than zero")
        self._native = native
        self._generation = generation
        self._active = True

    @classmethod
    def _from_native(
        cls,
        native: AdbServerNativeHandle,
        generation: int,
    ) -> "AdbOwnedServer":
        return cls(native, generation, _token=_OWNER_CONSTRUCTION_TOKEN)

    @property
    def endpoint(self) -> AdbServerEndpoint:
        return self._native.endpoint

    @property
    def generation(self) -> int:
        """Process-local identity of this exact native server lifetime."""

        return self._generation

    @property
    def active(self) -> bool:
        """Whether this owner remains unfenced and its exact native lifetime is alive."""

        return self._active and self._native.active

    def _mark_lost(self) -> bool:
        if not self._active:
            return False
        self._active = False
        return True

    @property
    def _native_handle(self) -> AdbServerNativeHandle:
        return self._native


class _DefaultAdbServerLauncher:
    """Lazily construct the concrete launcher after the ADB package graph is imported."""

    def __init__(self) -> None:
        self._delegate: AdbServerLauncher | None = None

    def launch(self) -> AdbServerNativeHandle:
        delegate = self._delegate
        if delegate is None:
            from adb.server.lifecycle.adapters import SubprocessAdbServerLauncher

            delegate = SubprocessAdbServerLauncher()
            self._delegate = delegate
        return delegate.launch()


class _ProcessAdbServerOwnerStatus(str, Enum):
    ABSENT = "absent"
    STARTING = "starting"
    ACTIVE = "active"
    CLOSING = "closing"


class _ProcessAdbServerOwner:
    """Serialize all process-owned ADB server generations through one launcher.

    The launcher owns endpoint configuration and OS creation authority. This state machine owns
    only process-singleton lifetime serialization and stale-generation fencing.
    """

    def __init__(self, launcher: AdbServerLauncher | None = None) -> None:
        if launcher is None:
            launcher = _DefaultAdbServerLauncher()
        if not isinstance(launcher, AdbServerLauncher):
            raise TypeError("launcher must satisfy AdbServerLauncher")
        self._launcher = launcher
        self._condition = Condition()
        self._status = _ProcessAdbServerOwnerStatus.ABSENT
        self._owner: AdbOwnedServer | None = None
        self._generation = 0

    def acquire(self) -> AdbOwnedServer:
        """Return the active generation or launch one fresh process-owned server."""

        while True:
            stale: AdbOwnedServer | None = None
            with self._condition:
                while self._status in {
                    _ProcessAdbServerOwnerStatus.STARTING,
                    _ProcessAdbServerOwnerStatus.CLOSING,
                }:
                    self._condition.wait()

                if self._status is _ProcessAdbServerOwnerStatus.ACTIVE:
                    assert self._owner is not None
                    if self._owner.active:
                        return self._owner
                    stale = self._owner
                    stale._mark_lost()
                    self._status = _ProcessAdbServerOwnerStatus.CLOSING
                else:
                    self._status = _ProcessAdbServerOwnerStatus.STARTING

            if stale is not None:
                self._dispose_current(stale)
                continue
            break

        try:
            native = self._launcher.launch()
            if not isinstance(native, AdbServerNativeHandle):
                raise TypeError("launcher.launch() must return AdbServerNativeHandle")
        except BaseException:
            with self._condition:
                self._status = _ProcessAdbServerOwnerStatus.ABSENT
                self._owner = None
                self._condition.notify_all()
            raise

        with self._condition:
            self._generation += 1
            owner = AdbOwnedServer._from_native(native, self._generation)
            self._owner = owner
            self._status = _ProcessAdbServerOwnerStatus.ACTIVE
            self._condition.notify_all()
            return owner

    def invalidate(self, owner: AdbOwnedServer) -> bool:
        """Fence and dispose the current generation after terminal liveness evidence.

        Repeated invalidation of an already-retired owner is a no-op. It can never dispose a
        newer generation because teardown authority is stored on the referenced owner itself.
        """

        self._require_owner(owner)
        with self._condition:
            if (
                self._status is _ProcessAdbServerOwnerStatus.CLOSING
                and self._owner is owner
            ):
                should_dispose = True
            elif (
                self._status is _ProcessAdbServerOwnerStatus.ACTIVE
                and self._owner is owner
            ):
                owner._mark_lost()
                self._status = _ProcessAdbServerOwnerStatus.CLOSING
                should_dispose = True
            elif not owner.active:
                return False
            else:
                raise self._stale_owner_error(owner)

        if should_dispose:
            self._dispose_current(owner)
            return True
        return False  # pragma: no cover - exhaustive state guard

    def close(self, owner: AdbOwnedServer) -> None:
        """Orderly close of the current generation using its exact native handle."""

        self._require_owner(owner)
        with self._condition:
            if (
                self._status is _ProcessAdbServerOwnerStatus.CLOSING
                and self._owner is owner
            ):
                pass
            elif (
                self._status is _ProcessAdbServerOwnerStatus.ACTIVE
                and self._owner is owner
            ):
                owner._mark_lost()
                self._status = _ProcessAdbServerOwnerStatus.CLOSING
            else:
                raise self._stale_owner_error(owner)

        self._dispose_current(owner)

    @property
    def active_owner(self) -> AdbOwnedServer | None:
        """Return the current live owner without launching a new generation."""

        with self._condition:
            if self._status is not _ProcessAdbServerOwnerStatus.ACTIVE:
                return None
            assert self._owner is not None
            return self._owner if self._owner.active else None

    def _dispose_current(self, owner: AdbOwnedServer) -> None:
        """Dispose one fenced owner and publish ABSENT only after close is proven."""

        owner._native_handle.close()
        with self._condition:
            if self._owner is owner:
                self._owner = None
                self._status = _ProcessAdbServerOwnerStatus.ABSENT
                self._condition.notify_all()

    @staticmethod
    def _require_owner(owner: object) -> None:
        if not isinstance(owner, AdbOwnedServer):
            raise TypeError("owner must be AdbOwnedServer")

    def _stale_owner_error(self, owner: AdbOwnedServer) -> AdbServerStaleOwnerError:
        current = self._owner
        current_generation = current.generation if current is not None else None
        return AdbServerStaleOwnerError(
            "ADB server generation "
            f"{owner.generation} is stale; current generation is {current_generation!r}"
        )


_PROCESS_ADB_SERVER_OWNER = _ProcessAdbServerOwner()


def acquire_process_adb_server() -> AdbOwnedServer:
    """Acquire or create the single process-owned ADB server generation."""

    return _PROCESS_ADB_SERVER_OWNER.acquire()


def invalidate_process_adb_server(owner: AdbOwnedServer) -> bool:
    """Fence and dispose the current process-owned server after ownership loss."""

    return _PROCESS_ADB_SERVER_OWNER.invalidate(owner)


def close_process_adb_server(owner: AdbOwnedServer) -> None:
    """Close the current process-owned server through its exact native lifetime handle."""

    _PROCESS_ADB_SERVER_OWNER.close(owner)


__all__ = [
    "AdbOwnedServer",
    "AdbServerOwnershipLostError",
    "AdbServerStaleOwnerError",
    "acquire_process_adb_server",
    "close_process_adb_server",
    "invalidate_process_adb_server",
]
