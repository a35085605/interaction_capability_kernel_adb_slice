from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from threading import Lock
from typing import Protocol, TypeAlias, runtime_checkable

from adb.server.identity import AdbServerIdentity
from adb.transport_list.identity import AdbTransportListIdentity
from adb.transport_list.model import AdbTransportList
from adb.transport_list.observation import AdbTransportListObservation


class AdbTransportListStateStatus(str, Enum):
    """Visibility status of the runtime-authoritative transport-list state."""

    CURRENT = "current"
    INVALIDATED = "invalidated"


@dataclass(frozen=True, slots=True, init=False)
class AdbTransportListState:
    """Immutable authoritative projection of one runtime observation.

    Invalidated state retains the last committed observation for stale-work fencing.
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
    def transport_list(self) -> AdbTransportList | None:
        """Return retained transport-list evidence, including invalidated evidence."""

        observation = self.observation
        return None if observation is None else observation.transport_list

    @property
    def identity(self) -> AdbTransportListIdentity | None:
        """Return the retained observation identity watermark."""

        observation = self.observation
        return None if observation is None else observation.identity

    @property
    def server(self) -> AdbServerIdentity | None:
        """Return the server lifetime that produced the retained observation."""

        observation = self.observation
        return None if observation is None else observation.server

    @property
    def current_observation(self) -> AdbTransportListObservation | None:
        """Return the current authoritative observation, if one is visible."""

        return self.observation if self.status is AdbTransportListStateStatus.CURRENT else None

    @property
    def current(self) -> AdbTransportList | None:
        """Return the current authoritative transport list, if one is visible."""

        observation = self.current_observation
        return None if observation is None else observation.transport_list

    @property
    def current_identity(self) -> AdbTransportListIdentity | None:
        """Return the current authoritative transport-list identity, if visible."""

        observation = self.current_observation
        return None if observation is None else observation.identity


@dataclass(frozen=True, slots=True)
class AdbTransportListObserved:
    """Evidence that an observation transition committed this authoritative state."""

    state: AdbTransportListState

    def __post_init__(self) -> None:
        if not isinstance(self.state, AdbTransportListState):
            raise TypeError("state must be AdbTransportListState")
        if self.state.current_observation is None:
            raise ValueError("observed result requires current transport-list state")

    @property
    def observation(self) -> AdbTransportListObservation:
        observation = self.state.current_observation
        assert observation is not None
        return observation

    @property
    def transport_list(self) -> AdbTransportList:
        return self.observation.transport_list

    @property
    def identity(self) -> AdbTransportListIdentity:
        return self.observation.identity

    @property
    def server(self) -> AdbServerIdentity:
        return self.observation.server

    def __bool__(self) -> bool:
        return True


@dataclass(frozen=True, slots=True)
class AdbTransportListObservationStateConflict:
    """Evidence that an observation lost its expected authoritative-state fence."""

    observation: AdbTransportListObservation
    state: AdbTransportListState

    def __post_init__(self) -> None:
        if not isinstance(self.observation, AdbTransportListObservation):
            raise TypeError("observation must be AdbTransportListObservation")
        if not isinstance(self.state, AdbTransportListState):
            raise TypeError("state must be AdbTransportListState")

    def __bool__(self) -> bool:
        return False


AdbTransportListObservationResult: TypeAlias = (
    AdbTransportListObserved | AdbTransportListObservationStateConflict
)


@dataclass(frozen=True, slots=True)
class AdbTransportListInvalidated:
    """Evidence that invalidation committed while preserving last committed evidence."""

    state: AdbTransportListState

    def __post_init__(self) -> None:
        if not isinstance(self.state, AdbTransportListState):
            raise TypeError("state must be AdbTransportListState")
        if self.state.status is not AdbTransportListStateStatus.INVALIDATED:
            raise ValueError("invalidated result requires invalidated transport-list state")
        if self.state.observation is None:
            raise ValueError("invalidated result requires preserved transport-list observation")

    @property
    def observation(self) -> AdbTransportListObservation:
        observation = self.state.observation
        assert observation is not None
        return observation

    @property
    def identity(self) -> AdbTransportListIdentity:
        return self.observation.identity

    @property
    def server(self) -> AdbServerIdentity:
        return self.observation.server

    @property
    def transport_list(self) -> AdbTransportList:
        return self.observation.transport_list

    def __bool__(self) -> bool:
        return True


@dataclass(frozen=True, slots=True)
class AdbTransportListInvalidationStateConflict:
    """Evidence that invalidation lost its expected current-identity fence."""

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
    """Authoritative transport-list state view for one runtime."""

    @property
    def identity(self) -> AdbTransportListIdentity | None: ...

    @property
    def status(self) -> AdbTransportListStateStatus: ...

    @property
    def current(self) -> AdbTransportList | None: ...

    @property
    def current_identity(self) -> AdbTransportListIdentity | None: ...

    def snapshot(self) -> AdbTransportListState: ...


@runtime_checkable
class AdbTransportListStateWriter(Protocol):
    """Apply authoritative transport-list observation and invalidation transitions."""

    def invalidate(
        self,
        expected: AdbTransportListIdentity,
    ) -> AdbTransportListInvalidationResult: ...

    def observe(
        self,
        observation: AdbTransportListObservation,
        expected: AdbTransportListState,
    ) -> AdbTransportListObservationResult: ...


class AdbTransportListStateStore(AdbTransportListStateView, AdbTransportListStateWriter):
    """Thread-safe authority for transport-list state transitions."""

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
    def observation(self) -> AdbTransportListObservation | None:
        return self.state.observation

    @property
    def transport_list(self) -> AdbTransportList | None:
        return self.state.transport_list

    @property
    def identity(self) -> AdbTransportListIdentity | None:
        return self.state.identity

    @property
    def server(self) -> AdbServerIdentity | None:
        return self.state.server

    @property
    def status(self) -> AdbTransportListStateStatus:
        return self.state.status

    @property
    def current_observation(self) -> AdbTransportListObservation | None:
        return self.state.current_observation

    @property
    def current(self) -> AdbTransportList | None:
        return self.state.current

    @property
    def current_identity(self) -> AdbTransportListIdentity | None:
        return self.state.current_identity

    def snapshot(self) -> AdbTransportListState:
        """Atomically capture the current immutable authoritative state value."""

        return self.state

    def invalidate(
        self,
        expected: AdbTransportListIdentity,
    ) -> AdbTransportListInvalidationResult:
        """Invalidate ``expected`` iff it is the current authoritative list identity."""

        if not isinstance(expected, AdbTransportListIdentity):
            raise TypeError("expected must be AdbTransportListIdentity")

        with self._lock:
            current = self._state
            if current.current_identity != expected:
                return AdbTransportListInvalidationStateConflict(current)
            observation = current.observation
            assert observation is not None
            next_state = AdbTransportListState(
                observation=observation,
                status=AdbTransportListStateStatus.INVALIDATED,
            )
            self._state = next_state
            return AdbTransportListInvalidated(next_state)

    def observe(
        self,
        observation: AdbTransportListObservation,
        expected: AdbTransportListState,
    ) -> AdbTransportListObservationResult:
        """Commit an already-identified observation when ``expected`` is authoritative."""

        if not isinstance(observation, AdbTransportListObservation):
            raise TypeError("observation must be AdbTransportListObservation")
        if not isinstance(expected, AdbTransportListState):
            raise TypeError("expected must be AdbTransportListState")

        with self._lock:
            current = self._state
            if current != expected:
                return AdbTransportListObservationStateConflict(observation, current)
            next_state = AdbTransportListState(
                observation=observation,
                status=AdbTransportListStateStatus.CURRENT,
            )
            self._state = next_state
            return AdbTransportListObserved(next_state)


__all__ = [
    "AdbTransportListInvalidated",
    "AdbTransportListInvalidationResult",
    "AdbTransportListInvalidationStateConflict",
    "AdbTransportListObservationResult",
    "AdbTransportListObservationStateConflict",
    "AdbTransportListObserved",
    "AdbTransportListState",
    "AdbTransportListStateStatus",
    "AdbTransportListStateStore",
    "AdbTransportListStateView",
    "AdbTransportListStateWriter",
]
