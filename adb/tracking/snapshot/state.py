from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from threading import Lock
from typing import Protocol, TypeAlias, runtime_checkable

from adb.server.identity import AdbServerIdentity
from adb.tracking.snapshot.identity import AdbTransportListSnapshot, AdbTransportListSnapshotEpoch


@dataclass(frozen=True, slots=True)
class AdbTransportListObservation:
    """One complete transport-list snapshot bound to its source server lifetime."""

    server: AdbServerIdentity
    snapshot: AdbTransportListSnapshot

    def __post_init__(self) -> None:
        if not isinstance(self.server, AdbServerIdentity):
            raise TypeError("server must be AdbServerIdentity")
        if not isinstance(self.snapshot, AdbTransportListSnapshot):
            raise TypeError("snapshot must be AdbTransportListSnapshot")

    @property
    def epoch(self) -> AdbTransportListSnapshotEpoch:
        """Runtime-scoped identity of the underlying snapshot."""

        return self.snapshot.epoch


class AdbTransportListStateStatus(str, Enum):
    """Visibility status of the runtime-authoritative transport-list state."""

    CURRENT = "current"
    INVALIDATED = "invalidated"


@dataclass(frozen=True, slots=True, init=False)
class AdbTransportListState:
    """Immutable authoritative transport-list state for one runtime observation.

    ``observation`` preserves the last committed server-bound snapshot after invalidation. This
    retained observation carries the runtime snapshot-epoch watermark used to reject stale work,
    while ``current`` exposes it only when the state is current.
    """

    observation: AdbTransportListObservation | None = None
    status: AdbTransportListStateStatus = AdbTransportListStateStatus.INVALIDATED

    def __init__(
        self,
        observation: AdbTransportListObservation | None = None,
        status: AdbTransportListStateStatus | None = None,
    ) -> None:
        if status is None:
            status = (
                AdbTransportListStateStatus.CURRENT
                if observation is not None
                else AdbTransportListStateStatus.INVALIDATED
            )
        object.__setattr__(self, "observation", observation)
        object.__setattr__(self, "status", status)
        self.__post_init__()

    def __post_init__(self) -> None:
        if self.observation is not None and not isinstance(
            self.observation, AdbTransportListObservation
        ):
            raise TypeError("observation must be AdbTransportListObservation or None")
        if not isinstance(self.status, AdbTransportListStateStatus):
            raise TypeError("status must be AdbTransportListStateStatus")
        if self.status is AdbTransportListStateStatus.CURRENT and self.observation is None:
            raise ValueError("current transport-list state must have an observation")

    @property
    def current(self) -> AdbTransportListObservation | None:
        """Return the current authoritative observation, if one is visible."""

        return (
            self.observation
            if self.status is AdbTransportListStateStatus.CURRENT
            else None
        )

    @property
    def latest_epoch(self) -> AdbTransportListSnapshotEpoch | None:
        """Return the last committed snapshot epoch, including invalidated evidence."""

        observation = self.observation
        return None if observation is None else observation.epoch


@dataclass(frozen=True, slots=True)
class AdbTransportListObserved:
    """Evidence that an observation transition committed this authoritative state."""

    state: AdbTransportListState

    def __post_init__(self) -> None:
        if not isinstance(self.state, AdbTransportListState):
            raise TypeError("state must be AdbTransportListState")
        if self.state.current is None:
            raise ValueError("observed result requires current transport-list state")

    @property
    def observation(self) -> AdbTransportListObservation:
        observation = self.state.current
        assert observation is not None
        return observation

    def __bool__(self) -> bool:
        return True


@dataclass(frozen=True, slots=True)
class AdbTransportListObservationStateConflict:
    """Evidence that observation lost the runtime snapshot-epoch advancement fence."""

    state: AdbTransportListState

    def __post_init__(self) -> None:
        if not isinstance(self.state, AdbTransportListState):
            raise TypeError("state must be AdbTransportListState")

    def __bool__(self) -> bool:
        return False


AdbTransportListObservationResult: TypeAlias = (
    AdbTransportListObserved | AdbTransportListObservationStateConflict
)


@dataclass(frozen=True, slots=True)
class AdbTransportListInvalidated:
    """Evidence that invalidation committed while preserving the last observation watermark."""

    state: AdbTransportListState

    def __post_init__(self) -> None:
        if not isinstance(self.state, AdbTransportListState):
            raise TypeError("state must be AdbTransportListState")
        if self.state.status is not AdbTransportListStateStatus.INVALIDATED:
            raise ValueError("invalidated result requires invalidated transport-list state")
        if self.state.observation is None:
            raise ValueError("invalidated result requires preserved observation evidence")

    def __bool__(self) -> bool:
        return True


@dataclass(frozen=True, slots=True)
class AdbTransportListInvalidationStateConflict:
    """Evidence that invalidation lost its expected authoritative-state fence."""

    state: AdbTransportListState

    def __post_init__(self) -> None:
        if not isinstance(self.state, AdbTransportListState):
            raise TypeError("state must be AdbTransportListState")

    def __bool__(self) -> bool:
        return False


AdbTransportListInvalidationResult: TypeAlias = (
    AdbTransportListInvalidated | AdbTransportListInvalidationStateConflict
)


@runtime_checkable
class AdbTransportListStateView(Protocol):
    """Authoritative server-bound transport-list state view for one runtime."""

    @property
    def status(self) -> AdbTransportListStateStatus: ...

    @property
    def current(self) -> AdbTransportListObservation | None: ...

    @property
    def latest_epoch(self) -> AdbTransportListSnapshotEpoch | None: ...

    def snapshot(self) -> AdbTransportListState: ...


@runtime_checkable
class AdbTransportListStateWriter(Protocol):
    """Apply authoritative transport-list observation and invalidation transitions."""

    def invalidate(
        self,
        expected: AdbTransportListState,
    ) -> AdbTransportListInvalidationResult: ...

    def observe(
        self,
        observation: AdbTransportListObservation,
    ) -> AdbTransportListObservationResult: ...


class AdbTransportListStateStore(AdbTransportListStateView, AdbTransportListStateWriter):
    """Thread-safe authority for one runtime's immutable :class:`AdbTransportListState` value."""

    def __init__(self, initial: AdbTransportListState | None = None) -> None:
        if initial is None:
            state = AdbTransportListState()
        elif isinstance(initial, AdbTransportListState):
            state = initial
        else:
            raise TypeError("initial must be AdbTransportListState or None")
        self._lock = Lock()
        self._state = state

    @property
    def state(self) -> AdbTransportListState:
        """Atomically return the current immutable authoritative state value."""

        with self._lock:
            return self._state

    @property
    def status(self) -> AdbTransportListStateStatus:
        return self.state.status

    @property
    def current(self) -> AdbTransportListObservation | None:
        return self.state.current

    @property
    def latest_epoch(self) -> AdbTransportListSnapshotEpoch | None:
        return self.state.latest_epoch

    def snapshot(self) -> AdbTransportListState:
        """Atomically capture the current immutable authoritative state value."""

        return self.state

    def invalidate(
        self,
        expected: AdbTransportListState,
    ) -> AdbTransportListInvalidationResult:
        """Invalidate ``expected`` iff it is the current authoritative visible state."""

        if not isinstance(expected, AdbTransportListState):
            raise TypeError("expected must be AdbTransportListState")
        if expected.current is None:
            raise ValueError("expected transport-list state must be current")

        with self._lock:
            current = self._state
            if current != expected:
                return AdbTransportListInvalidationStateConflict(current)

            observation = expected.observation
            assert observation is not None
            next_state = AdbTransportListState(
                observation,
                AdbTransportListStateStatus.INVALIDATED,
            )
            self._state = next_state
            return AdbTransportListInvalidated(next_state)

    def invalidate_current(self) -> None:
        """Compatibility facade that invalidates the currently visible state when present."""

        while True:
            expected = self.snapshot()
            if expected.current is None:
                return
            result = self.invalidate(expected)
            if isinstance(result, AdbTransportListInvalidated):
                return

    def observe(
        self,
        observation: AdbTransportListObservation,
    ) -> AdbTransportListObservationResult:
        """Commit one server-bound observation when its snapshot epoch advances runtime state."""

        if not isinstance(observation, AdbTransportListObservation):
            raise TypeError("observation must be AdbTransportListObservation")
        with self._lock:
            current = self._state
            latest_epoch = current.latest_epoch
            if latest_epoch is not None and observation.epoch <= latest_epoch:
                return AdbTransportListObservationStateConflict(current)

            next_state = AdbTransportListState(
                observation,
                AdbTransportListStateStatus.CURRENT,
            )
            self._state = next_state
            return AdbTransportListObserved(next_state)


# Compatibility aliases for the pre-State/Store naming. New code should use the names above.
AdbTransportListSnapshotState = AdbTransportListStateStore
AdbTransportListSnapshotView = AdbTransportListStateView
AdbTransportListSnapshotWriter = AdbTransportListStateWriter


__all__ = [
    "AdbTransportListInvalidated",
    "AdbTransportListInvalidationResult",
    "AdbTransportListInvalidationStateConflict",
    "AdbTransportListObservation",
    "AdbTransportListObservationResult",
    "AdbTransportListObservationStateConflict",
    "AdbTransportListObserved",
    "AdbTransportListSnapshotState",
    "AdbTransportListSnapshotView",
    "AdbTransportListSnapshotWriter",
    "AdbTransportListState",
    "AdbTransportListStateStatus",
    "AdbTransportListStateStore",
    "AdbTransportListStateView",
    "AdbTransportListStateWriter",
]
