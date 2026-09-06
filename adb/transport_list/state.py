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
from adb.transport_list.observation import (
    AdbTransportListObservation,
    AdbTransportListObservationBasis,
)


class AdbTransportListStateStatus(str, Enum):
    """Visibility status of the runtime-authoritative transport-list state."""

    CURRENT = "current"
    INVALIDATED = "invalidated"


@dataclass(frozen=True, slots=True, init=False)
class AdbTransportListState:
    """Immutable authoritative transport-list state for one runtime.

    Invalidated state retains the last committed identity and transport list for stale-work
    fencing while observation provenance remains outside authoritative state.
    """

    identity: AdbTransportListIdentity | None = None
    transport_list: AdbTransportList | None = None
    status: AdbTransportListStateStatus = AdbTransportListStateStatus.INVALIDATED

    def __init__(
        self,
        identity: AdbTransportListIdentity | None = None,
        transport_list: AdbTransportList | None = None,
        status: AdbTransportListStateStatus | None = None,
    ) -> None:
        if status is None:
            status = (
                AdbTransportListStateStatus.CURRENT
                if identity is not None or transport_list is not None
                else AdbTransportListStateStatus.INVALIDATED
            )
        object.__setattr__(self, "identity", identity)
        object.__setattr__(self, "transport_list", transport_list)
        object.__setattr__(self, "status", status)
        self.__post_init__()

    def __post_init__(self) -> None:
        if self.identity is not None and not isinstance(
            self.identity, AdbTransportListIdentity
        ):
            raise TypeError("identity must be AdbTransportListIdentity or None")
        if self.transport_list is not None and not isinstance(
            self.transport_list, AdbTransportList
        ):
            raise TypeError("transport_list must be AdbTransportList or None")
        if not isinstance(self.status, AdbTransportListStateStatus):
            raise TypeError("status must be AdbTransportListStateStatus")
        if (self.identity is None) != (self.transport_list is None):
            raise ValueError("transport-list identity and data must be present together")
        if self.status is AdbTransportListStateStatus.CURRENT and self.identity is None:
            raise ValueError("current transport-list state must have identity and data")

    @property
    def current(self) -> AdbTransportList | None:
        """Return the current authoritative transport list, if one is visible."""

        return (
            self.transport_list
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
    """Signal that one transport-list observation committed as authoritative."""

    observation: AdbTransportListObservation

    def __post_init__(self) -> None:
        if not isinstance(self.observation, AdbTransportListObservation):
            raise TypeError("observation must be AdbTransportListObservation")

    @property
    def identity(self) -> AdbTransportListIdentity:
        """Identity issued by the state authority for the committed observation."""

        return self.observation.identity

    def __bool__(self) -> bool:
        return True


@dataclass(frozen=True, slots=True)
class AdbTransportListObservationStateConflict:
    """Evidence that a raw observation lost its authoritative transport-list-state fence."""

    basis: AdbTransportListObservationBasis
    transport_list: AdbTransportList
    state: AdbTransportListState

    def __post_init__(self) -> None:
        if not isinstance(self.basis, AdbTransportListObservationBasis):
            raise TypeError("basis must be AdbTransportListObservationBasis")
        if not isinstance(self.transport_list, AdbTransportList):
            raise TypeError("transport_list must be AdbTransportList")
        if not isinstance(self.state, AdbTransportListState):
            raise TypeError("state must be AdbTransportListState")

    def __bool__(self) -> bool:
        return False


AdbTransportListObservationResult: TypeAlias = (
    AdbTransportListObserved | AdbTransportListObservationStateConflict
)


@dataclass(frozen=True, slots=True)
class AdbTransportListInvalidated:
    """Signal that one transport-list identity committed as invalidated."""

    identity: AdbTransportListIdentity

    def __post_init__(self) -> None:
        if not isinstance(self.identity, AdbTransportListIdentity):
            raise TypeError("identity must be AdbTransportListIdentity")

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
        basis: AdbTransportListObservationBasis,
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
    def transport_list(self) -> AdbTransportList | None:
        return self.state.transport_list

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
            assert transport_list is not None
            next_state = AdbTransportListState(
                identity=expected,
                transport_list=transport_list,
                status=AdbTransportListStateStatus.INVALIDATED,
            )
            self._state = next_state
            return AdbTransportListInvalidated(expected)

    def observe(
        self,
        basis: AdbTransportListObservationBasis,
        transport_list: AdbTransportList,
        expected: AdbTransportListState,
    ) -> AdbTransportListObservationResult:
        """Commit raw transport-list data when ``expected`` is authoritative and basis-matched.

        The fresh transport-list identity is issued only after both state fences succeed and
        immediately before the new authoritative state is committed.
        """

        if not isinstance(basis, AdbTransportListObservationBasis):
            raise TypeError("basis must be AdbTransportListObservationBasis")
        if not isinstance(transport_list, AdbTransportList):
            raise TypeError("transport_list must be AdbTransportList")
        if not isinstance(expected, AdbTransportListState):
            raise TypeError("expected must be AdbTransportListState")

        with self._lock:
            current = self._state
            if current != expected or basis.transport_list_identity != current.identity:
                return AdbTransportListObservationStateConflict(
                    basis=basis,
                    transport_list=transport_list,
                    state=current,
                )

            identity = self._identity_issuer.issue()
            observation = AdbTransportListObservation(
                basis=basis,
                identity=identity,
                transport_list=transport_list,
            )
            next_state = AdbTransportListState(
                identity=identity,
                transport_list=transport_list,
                status=AdbTransportListStateStatus.CURRENT,
            )
            self._state = next_state
            return AdbTransportListObserved(observation)


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
