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
from adb.transport_list.observation import AdbTransportListObservation
from adb.transport_list.session_identity import AdbTransportListSessionIdentity


class AdbTransportListStateStatus(str, Enum):
    """Visibility status of the authoritative transport-list state."""

    CURRENT = "current"
    INVALIDATED = "invalidated"


@dataclass(frozen=True, slots=True, init=False)
class AdbTransportListState:
    """Immutable authoritative transport-list projection state.

    Invalidated state may retain the last committed identity/data as historical evidence. Producer
    admission and observation fencing are tracked independently; watch lifecycle authority belongs
    to the transport-list watch backend.
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
                if identity is not None and transport_list is not None
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
        if (
            self.status is AdbTransportListStateStatus.CURRENT
            and self.identity is None
        ):
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
        return self.observation.identity

    @property
    def session(self) -> AdbTransportListSessionIdentity:
        return self.observation.session

    def __bool__(self) -> bool:
        return True


@dataclass(frozen=True, slots=True)
class AdbTransportListObservationStateConflict:
    """Evidence that a raw observation lost its runtime session/status authority fence."""

    session: AdbTransportListSessionIdentity
    transport_list: AdbTransportList
    state: AdbTransportListState

    def __post_init__(self) -> None:
        if not isinstance(self.session, AdbTransportListSessionIdentity):
            raise TypeError("session must be AdbTransportListSessionIdentity")
        if not isinstance(self.transport_list, AdbTransportList):
            raise TypeError("transport_list must be AdbTransportList")
        if not isinstance(self.state, AdbTransportListState):
            raise TypeError("state must be AdbTransportListState")

    def __bool__(self) -> bool:
        return False


AdbTransportListObservationResult: TypeAlias = (
    AdbTransportListObserved | AdbTransportListObservationStateConflict
)
AdbTransportListCoordinatedObservationResult: TypeAlias = AdbTransportListObservationResult


@dataclass(frozen=True, slots=True)
class AdbTransportListSessionBegun:
    """Evidence that a fresh producer session acquired transport-list authority."""

    session: AdbTransportListSessionIdentity
    superseded_session: AdbTransportListSessionIdentity | None
    invalidated_identity: AdbTransportListIdentity | None

    def __post_init__(self) -> None:
        if not isinstance(self.session, AdbTransportListSessionIdentity):
            raise TypeError("session must be AdbTransportListSessionIdentity")
        if self.superseded_session is not None and not isinstance(
            self.superseded_session, AdbTransportListSessionIdentity
        ):
            raise TypeError(
                "superseded_session must be AdbTransportListSessionIdentity or None"
            )
        if self.invalidated_identity is not None and not isinstance(
            self.invalidated_identity, AdbTransportListIdentity
        ):
            raise TypeError("invalidated_identity must be AdbTransportListIdentity or None")

    def __bool__(self) -> bool:
        return True


@dataclass(frozen=True, slots=True)
class AdbTransportListSessionRevoked:
    """Evidence that one producer session lost transport-list authority."""

    session: AdbTransportListSessionIdentity
    invalidated_identity: AdbTransportListIdentity | None

    def __post_init__(self) -> None:
        if not isinstance(self.session, AdbTransportListSessionIdentity):
            raise TypeError("session must be AdbTransportListSessionIdentity")
        if self.invalidated_identity is not None and not isinstance(
            self.invalidated_identity, AdbTransportListIdentity
        ):
            raise TypeError("invalidated_identity must be AdbTransportListIdentity or None")

    def __bool__(self) -> bool:
        return True


@dataclass(frozen=True, slots=True)
class AdbTransportListSessionRevocationStateConflict:
    """Evidence that a stale producer session had no authority to revoke."""

    session: AdbTransportListSessionIdentity

    def __post_init__(self) -> None:
        if not isinstance(self.session, AdbTransportListSessionIdentity):
            raise TypeError("session must be AdbTransportListSessionIdentity")

    def __bool__(self) -> bool:
        return False


AdbTransportListSessionRevocationResult: TypeAlias = (
    AdbTransportListSessionRevoked | AdbTransportListSessionRevocationStateConflict
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
    """Read the authoritative transport-list projection state."""

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
class AdbTransportListSessionAuthority(Protocol):
    """Command boundary for producer-session-fenced transport-list observations.

    Watch lifecycle state belongs to ``AdbTransportListWatchBackend``. This authority
    admits producer identities and fences projection mutations without exposing a second
    lifecycle state model.
    """

    def begin_session(self) -> AdbTransportListSessionBegun | None: ...

    def can_observe_update(
        self,
        session: AdbTransportListSessionIdentity,
    ) -> bool: ...

    def revoke_session(
        self,
        session: AdbTransportListSessionIdentity,
    ) -> AdbTransportListSessionRevocationResult: ...

    def observe_initial(
        self,
        session: AdbTransportListSessionIdentity,
        transport_list: AdbTransportList,
    ) -> AdbTransportListObservationResult: ...

    def observe_update(
        self,
        session: AdbTransportListSessionIdentity,
        transport_list: AdbTransportList,
    ) -> AdbTransportListObservationResult: ...


@runtime_checkable
class AdbTransportListStateWriter(Protocol):
    """Apply authoritative transport-list projection transitions."""

    def invalidate(
        self,
        expected: AdbTransportListIdentity,
    ) -> AdbTransportListInvalidationResult: ...

    def observe_initial(
        self,
        transport_list: AdbTransportList,
    ) -> AdbTransportListState | None: ...

    def observe_update(
        self,
        transport_list: AdbTransportList,
    ) -> AdbTransportListState | None: ...


class AdbTransportListStateStore(AdbTransportListStateView, AdbTransportListStateWriter):
    """Thread-safe authority for authoritative transport-list projection transitions."""

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
        return self.state

    def invalidate(
        self,
        expected: AdbTransportListIdentity,
    ) -> AdbTransportListInvalidationResult:
        if not isinstance(expected, AdbTransportListIdentity):
            raise TypeError("expected must be AdbTransportListIdentity")

        with self._lock:
            current = self._state
            if current.current_identity != expected:
                return AdbTransportListInvalidationStateConflict(current)
            self._state = self._invalidated_state(current)
            return AdbTransportListInvalidated(expected)

    def observe_initial(
        self,
        transport_list: AdbTransportList,
    ) -> AdbTransportListState | None:
        """Commit one ``INVALIDATED -> CURRENT`` projection transition."""

        return self._observe(
            transport_list,
            required_status=AdbTransportListStateStatus.INVALIDATED,
        )

    def observe_update(
        self,
        transport_list: AdbTransportList,
    ) -> AdbTransportListState | None:
        """Commit one ``CURRENT -> CURRENT`` projection transition."""

        return self._observe(
            transport_list,
            required_status=AdbTransportListStateStatus.CURRENT,
        )

    def _observe(
        self,
        transport_list: AdbTransportList,
        *,
        required_status: AdbTransportListStateStatus,
    ) -> AdbTransportListState | None:
        if not isinstance(transport_list, AdbTransportList):
            raise TypeError("transport_list must be AdbTransportList")
        if not isinstance(required_status, AdbTransportListStateStatus):
            raise TypeError("required_status must be AdbTransportListStateStatus")

        with self._lock:
            current = self._state
            if current.status is not required_status:
                return None

            identity = self._identity_issuer.issue()
            next_state = AdbTransportListState(
                identity=identity,
                transport_list=transport_list,
                status=AdbTransportListStateStatus.CURRENT,
            )
            self._state = next_state
            return next_state

    @staticmethod
    def _invalidated_state(current: AdbTransportListState) -> AdbTransportListState:
        return AdbTransportListState(
            identity=current.identity,
            transport_list=current.transport_list,
            status=AdbTransportListStateStatus.INVALIDATED,
        )


__all__ = [
    "AdbTransportListCoordinatedObservationResult",
    "AdbTransportListInvalidated",
    "AdbTransportListInvalidationResult",
    "AdbTransportListInvalidationStateConflict",
    "AdbTransportListObservationResult",
    "AdbTransportListObservationStateConflict",
    "AdbTransportListObserved",
    "AdbTransportListSessionAuthority",
    "AdbTransportListSessionBegun",
    "AdbTransportListSessionRevocationResult",
    "AdbTransportListSessionRevocationStateConflict",
    "AdbTransportListSessionRevoked",
    "AdbTransportListState",
    "AdbTransportListStateStatus",
    "AdbTransportListStateStore",
    "AdbTransportListStateView",
    "AdbTransportListStateWriter",
]
