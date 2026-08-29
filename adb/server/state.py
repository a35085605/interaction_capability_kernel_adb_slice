from __future__ import annotations

from dataclasses import dataclass
from threading import Lock
from typing import Protocol, runtime_checkable

from adb.server.identity import AdbServer, ServerEpoch


@dataclass(frozen=True, slots=True)
class AdbServerStateSnapshot:
    """Immutable T0 observation of one runtime's authoritative server state."""

    current: AdbServer | None
    latest_epoch: ServerEpoch | None
    revision: int

    def __post_init__(self) -> None:
        if self.current is not None and not isinstance(self.current, AdbServer):
            raise TypeError("current must be AdbServer or None")
        if self.latest_epoch is not None and not isinstance(self.latest_epoch, ServerEpoch):
            raise TypeError("latest_epoch must be ServerEpoch or None")
        if isinstance(self.revision, bool) or not isinstance(self.revision, int):
            raise TypeError("revision must be an integer")
        if self.revision < 0:
            raise ValueError("revision must be greater than or equal to zero")
        if self.current is not None and self.current.epoch != self.latest_epoch:
            raise ValueError("current server epoch must equal latest_epoch")


@dataclass(frozen=True, slots=True)
class AdbServerStateTransition:
    """Requested T0 -> T1 authoritative current-server transition."""

    before: AdbServerStateSnapshot
    after: AdbServer | None

    def __post_init__(self) -> None:
        if not isinstance(self.before, AdbServerStateSnapshot):
            raise TypeError("before must be AdbServerStateSnapshot")
        if self.after is not None and not isinstance(self.after, AdbServer):
            raise TypeError("after must be AdbServer or None")


@runtime_checkable
class AdbServerStateView(Protocol):
    """Read-only current ADB server lifetime projection for one runtime."""

    @property
    def current(self) -> AdbServer | None: ...

    @property
    def latest_epoch(self) -> ServerEpoch | None: ...


@runtime_checkable
class AdbServerStateWriter(Protocol):
    """Commit exact ADB server lifetime transitions for one runtime."""

    def commit(self, transition: AdbServerStateTransition) -> bool: ...

    def activate(self, server: AdbServer) -> bool: ...

    def retire(self, server: AdbServer) -> bool: ...


class AdbServerState(AdbServerStateView, AdbServerStateWriter):
    """Thread-safe authoritative current-server state for one runtime.

    State mutation is compare-and-commit against an immutable snapshot.  The monotonically
    increasing revision fences ABA sequences such as ``None -> server -> None`` while an external
    lifecycle side effect is in flight.  Epochs also advance monotonically: a lifetime must retire
    before a newer one can activate, and retired lifetimes cannot become current again.
    """

    def __init__(self, initial: AdbServer | None = None) -> None:
        if initial is not None and not isinstance(initial, AdbServer):
            raise TypeError("initial must be AdbServer or None")
        self._lock = Lock()
        self._current = initial
        self._latest_epoch = None if initial is None else initial.epoch
        self._revision = 0

    @property
    def current(self) -> AdbServer | None:
        with self._lock:
            return self._current

    @property
    def latest_epoch(self) -> ServerEpoch | None:
        with self._lock:
            return self._latest_epoch

    def snapshot(self) -> AdbServerStateSnapshot:
        """Atomically capture the T0 state used to fence a later lifecycle commit."""

        with self._lock:
            return self._snapshot_locked()

    def commit(self, transition: AdbServerStateTransition) -> bool:
        """Commit T1 only when the supplied T0 still exactly matches authoritative state."""

        if not isinstance(transition, AdbServerStateTransition):
            raise TypeError("transition must be AdbServerStateTransition")

        with self._lock:
            if transition.before != self._snapshot_locked():
                return False

            server = transition.after
            if server == self._current:
                return True

            if server is None:
                if self._current is None:
                    return True
                self._current = None
                self._revision += 1
                return True

            if self._current is not None:
                return False
            latest_epoch = self._latest_epoch
            if latest_epoch is not None and server.epoch <= latest_epoch:
                return False

            self._current = server
            self._latest_epoch = server.epoch
            self._revision += 1
            return True

    def activate(self, server: AdbServer) -> bool:
        """Compatibility helper that commits a fresh lifetime against an immediate T0 snapshot."""

        if not isinstance(server, AdbServer):
            raise TypeError("server must be AdbServer")
        return self.commit(AdbServerStateTransition(self.snapshot(), server))

    def retire(self, server: AdbServer) -> bool:
        """Compatibility helper that clears only the exact current server lifetime."""

        if not isinstance(server, AdbServer):
            raise TypeError("server must be AdbServer")
        before = self.snapshot()
        if before.current != server:
            return False
        return self.commit(AdbServerStateTransition(before, None))

    def _snapshot_locked(self) -> AdbServerStateSnapshot:
        return AdbServerStateSnapshot(
            current=self._current,
            latest_epoch=self._latest_epoch,
            revision=self._revision,
        )


__all__ = [
    "AdbServerState",
    "AdbServerStateSnapshot",
    "AdbServerStateTransition",
    "AdbServerStateView",
    "AdbServerStateWriter",
]
