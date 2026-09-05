from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from threading import Lock
from typing import Protocol, TypeAlias, runtime_checkable

from adb.transport_list.identity import (
    AdbTransportListIdentity,
    AdbTransportListIdentityIssuer,
)
from adb.transport_list.model import AdbTransportList


class AdbTransportListStateStatus(str, Enum):
    """Visibility status of the runtime-authoritative transport-list state."""

    CURRENT = "current"
    INVALIDATED = "invalidated"


@dataclass(frozen=True, slots=True, init=False)
class AdbTransportListState:
    """Immutable authoritative transport-list state for one runtime.

    Invalidated state retains the last committed list and identity for stale-work fencing.
    """

    transport_list: AdbTransportList | None = None
    identity: AdbTransportListIdentity | None = None
    status: AdbTransportListStateStatus = AdbTransportListStateStatus.INVALIDATED

    def __init__(
        self,
        transport_list: AdbTransportList | None = None,
        identity: AdbTransportListIdentity | None = None,
        status: AdbTransportListStateStatus | None = None,
    ) -> None:
        if status is None:
            status = (
                AdbTransportListStateStatus.CURRENT
                if transport_list is not None
                else AdbTransportListStateStatus.INVALIDATED
            )
        object.__setattr__(self, "transport_list", transport_list)
        object.__setattr__(self, "identity", identity)
        object.__setattr__(self, "status", status)
        self.__post_init__()

    def __post_init__(self) -> None:
        if self.transport_list is not None and not isinstance(
            self.transport_list, AdbTransportList
        ):
            raise TypeError("transport_list must be AdbTransportList or None")
        if self.identity is not None and not isinstance(
            self.identity, AdbTransportListIdentity
        ):
            raise TypeError("identity must be AdbTransportListIdentity or None")
        if not isinstance(self.status, AdbTransportListStateStatus):
            raise TypeError("status must be AdbTransportListStateStatus")
        if (self.transport_list is None) != (self.identity is None):
            raise ValueError("transport-list value and identity must be present together")
        if self.status is AdbTransportListStateStatus.CURRENT and self.transport_list is None:
            raise ValueError("current transport-list state must have a transport list and identity")

    @property
    def current(self) -> AdbTransportList | None:
        """Return the current authoritative transport list, if one is visible."""

        return self.transport_list if self.status is AdbTransportListStateStatus.CURRENT else None

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
    def transport_list(self) -> AdbTransportList:
        transport_list = self.state.current
        assert transport_list is not None
        return transport_list

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
        if self.state.transport_list is None or self.state.identity is None:
            raise ValueError("invalidated result requires preserved transport list and identity")

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
        transport_list: AdbTransportList,
        expected: AdbTransportListState,
    ) -> AdbTransportListObservationResult: ...


class AdbTransportListStateStore(AdbTransportListStateView, AdbTransportListStateWriter):
    """Thread-safe authority for transport-list state transitions and identity issuance."""

    def __init__(self, initial: AdbTransportListState | None = None) -> None:
        if initial is None:
            state = AdbTransportListState()
        elif isinstance(initial, AdbTransportListState):
            state = initial
        else:
            raise TypeError("initial must be AdbTransportListState or None")
        self._lock = Lock()
        self._state = state
        self._identity_issuer = AdbTransportListIdentityIssuer(after=state.identity)

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
            transport_list = current.transport_list
            identity = current.identity
            assert transport_list is not None and identity is not None
            next_state = AdbTransportListState(
                transport_list=transport_list,
                identity=identity,
                status=AdbTransportListStateStatus.INVALIDATED,
            )
            self._state = next_state
            return AdbTransportListInvalidated(next_state)

    def observe(
        self,
        transport_list: AdbTransportList,
        expected: AdbTransportListState,
    ) -> AdbTransportListObservationResult:
        """Commit ``transport_list`` when ``expected`` is authoritative.

        A fresh identity is issued only after the state fence succeeds, immediately before the
        accepted observation is committed.
        """

        if not isinstance(transport_list, AdbTransportList):
            raise TypeError("transport_list must be AdbTransportList")
        if not isinstance(expected, AdbTransportListState):
            raise TypeError("expected must be AdbTransportListState")

        with self._lock:
            current = self._state
            if current != expected:
                return AdbTransportListObservationStateConflict(current)
            identity = self._identity_issuer.issue()
            next_state = AdbTransportListState(
                transport_list=transport_list,
                identity=identity,
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
