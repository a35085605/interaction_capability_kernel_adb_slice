from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from threading import Lock
from typing import Protocol, TypeAlias, runtime_checkable

from adb.server.identity import AdbServerIdentity
from adb.tracking.identity import AdbTransportListIdentity, AdbTransportListIdentityIssuer
from adb.tracking.snapshot.model import AdbTransportListSnapshot


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


class AdbTransportListStateStatus(str, Enum):
    """Visibility status of the runtime-authoritative transport-list state."""

    CURRENT = "current"
    INVALIDATED = "invalidated"


@dataclass(frozen=True, slots=True, init=False)
class AdbTransportListState:
    """Immutable authoritative transport-list state for one runtime observation.

    ``observation`` and ``identity`` describe the last committed transport-list revision.
    Invalidated states preserve both values so visibility does not depend on erasing evidence.
    The preserved identity is the committed-revision watermark used to fence stale work.
    """

    observation: AdbTransportListObservation | None = None
    identity: AdbTransportListIdentity | None = None
    status: AdbTransportListStateStatus = AdbTransportListStateStatus.INVALIDATED

    def __init__(
        self,
        observation: AdbTransportListObservation | None = None,
        identity: AdbTransportListIdentity | None = None,
        status: AdbTransportListStateStatus | None = None,
    ) -> None:
        if status is None:
            status = (
                AdbTransportListStateStatus.CURRENT
                if observation is not None
                else AdbTransportListStateStatus.INVALIDATED
            )
        object.__setattr__(self, "observation", observation)
        object.__setattr__(self, "identity", identity)
        object.__setattr__(self, "status", status)
        self.__post_init__()

    def __post_init__(self) -> None:
        if self.observation is not None and not isinstance(
            self.observation, AdbTransportListObservation
        ):
            raise TypeError("observation must be AdbTransportListObservation or None")
        if self.identity is not None and not isinstance(
            self.identity, AdbTransportListIdentity
        ):
            raise TypeError("identity must be AdbTransportListIdentity or None")
        if not isinstance(self.status, AdbTransportListStateStatus):
            raise TypeError("status must be AdbTransportListStateStatus")
        if (self.observation is None) != (self.identity is None):
            raise ValueError("transport-list observation and identity must be present together")
        if self.status is AdbTransportListStateStatus.CURRENT and self.observation is None:
            raise ValueError("current transport-list state must have an observation and identity")

    @property
    def current(self) -> AdbTransportListObservation | None:
        """Return the current authoritative observation, if one is visible."""

        return (
            self.observation
            if self.status is AdbTransportListStateStatus.CURRENT
            else None
        )

    @property
    def current_identity(self) -> AdbTransportListIdentity | None:
        """Return the current authoritative transport-list identity, if visible."""

        return (
            self.identity
            if self.status is AdbTransportListStateStatus.CURRENT
            else None
        )


@dataclass(frozen=True, slots=True)
class AdbTransportListObserved:
    """Evidence that an observation transition committed this authoritative state."""

    state: AdbTransportListState

    def __post_init__(self) -> None:
        if not isinstance(self.state, AdbTransportListState):
            raise TypeError("state must be AdbTransportListState")
        if self.state.current is None or self.state.current_identity is None:
            raise ValueError("observed result requires current transport-list state")

    @property
    def observation(self) -> AdbTransportListObservation:
        observation = self.state.current
        assert observation is not None
        return observation

    @property
    def identity(self) -> AdbTransportListIdentity:
        identity = self.state.current_identity
        assert identity is not None
        return identity

    def __bool__(self) -> bool:
        return True


@dataclass(frozen=True, slots=True)
class AdbTransportListObservationStateConflict:
    """Evidence that observation lost its expected authoritative-state fence."""

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
    """Evidence that invalidation committed while preserving last committed evidence."""

    state: AdbTransportListState

    def __post_init__(self) -> None:
        if not isinstance(self.state, AdbTransportListState):
            raise TypeError("state must be AdbTransportListState")
        if self.state.status is not AdbTransportListStateStatus.INVALIDATED:
            raise ValueError("invalidated result requires invalidated transport-list state")
        if self.state.observation is None or self.state.identity is None:
            raise ValueError("invalidated result requires preserved observation and identity")

    @property
    def identity(self) -> AdbTransportListIdentity:
        identity = self.state.identity
        assert identity is not None
        return identity

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
    """Authoritative server-bound transport-list state view for one runtime."""

    @property
    def identity(self) -> AdbTransportListIdentity | None: ...

    @property
    def status(self) -> AdbTransportListStateStatus: ...

    @property
    def current(self) -> AdbTransportListObservation | None: ...

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
    """Thread-safe authority for one runtime's immutable :class:`AdbTransportListState` value."""

    def __init__(self, initial: AdbTransportListState | None = None) -> None:
        if initial is None:
            state = AdbTransportListState()
        elif isinstance(initial, AdbTransportListState):
            state = initial
        else:
            raise TypeError("initial must be AdbTransportListState or None")
        self._lock = Lock()
        self._identity_issuer = AdbTransportListIdentityIssuer()
        self._state = state

    @property
    def state(self) -> AdbTransportListState:
        """Atomically return the current immutable authoritative state value."""

        with self._lock:
            return self._state

    @property
    def identity(self) -> AdbTransportListIdentity | None:
        return self.state.identity

    @property
    def status(self) -> AdbTransportListStateStatus:
        return self.state.status

    @property
    def current(self) -> AdbTransportListObservation | None:
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
            identity = current.identity
            assert observation is not None and identity is not None
            next_state = AdbTransportListState(
                observation=observation,
                identity=identity,
                status=AdbTransportListStateStatus.INVALIDATED,
            )
            self._state = next_state
            return AdbTransportListInvalidated(next_state)

    def invalidate_current(self) -> None:
        """Compatibility facade that invalidates the currently visible state when present."""

        while True:
            expected = self.current_identity
            if expected is None:
                return
            result = self.invalidate(expected)
            if isinstance(result, AdbTransportListInvalidated):
                return

    def observe(
        self,
        observation: AdbTransportListObservation,
        expected: AdbTransportListState,
    ) -> AdbTransportListObservationResult:
        """Commit one observation iff ``expected`` is the current authoritative state."""

        if not isinstance(observation, AdbTransportListObservation):
            raise TypeError("observation must be AdbTransportListObservation")
        if not isinstance(expected, AdbTransportListState):
            raise TypeError("expected must be AdbTransportListState")

        with self._lock:
            current = self._state
            if current != expected:
                return AdbTransportListObservationStateConflict(current)
            previous_identity = expected.identity
            next_identity = (
                self._identity_issuer.initial()
                if previous_identity is None
                else self._identity_issuer.successor(previous_identity)
            )
            next_state = AdbTransportListState(
                observation=observation,
                identity=next_identity,
                status=AdbTransportListStateStatus.CURRENT,
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
