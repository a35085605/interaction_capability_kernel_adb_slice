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
from adb.transport_list.session_identity import (
    AdbTransportListSessionIdentity,
    AdbTransportListSessionIdentityIssuer,
)


class AdbTransportListStateStatus(str, Enum):
    """Visibility status of the authoritative transport-list state."""

    CURRENT = "current"
    INVALIDATED = "invalidated"


@dataclass(frozen=True, slots=True, init=False)
class AdbTransportListState:
    """Immutable authoritative transport-list state.

    ``session`` is the sole producer session currently authorized to establish or continue the
    projection. ``INVALIDATED`` may retain a session while its initial list is pending. Once that
    session is revoked, ``session`` becomes ``None`` while the last committed identity/data remain
    available only as historical evidence.
    """

    session: AdbTransportListSessionIdentity | None = None
    identity: AdbTransportListIdentity | None = None
    transport_list: AdbTransportList | None = None
    status: AdbTransportListStateStatus = AdbTransportListStateStatus.INVALIDATED

    def __init__(
        self,
        identity: AdbTransportListIdentity | None = None,
        transport_list: AdbTransportList | None = None,
        status: AdbTransportListStateStatus | None = None,
        *,
        session: AdbTransportListSessionIdentity | None = None,
    ) -> None:
        if status is None:
            status = (
                AdbTransportListStateStatus.CURRENT
                if session is not None
                and identity is not None
                and transport_list is not None
                else AdbTransportListStateStatus.INVALIDATED
            )
        object.__setattr__(self, "session", session)
        object.__setattr__(self, "identity", identity)
        object.__setattr__(self, "transport_list", transport_list)
        object.__setattr__(self, "status", status)
        self.__post_init__()

    def __post_init__(self) -> None:
        if self.session is not None and not isinstance(
            self.session, AdbTransportListSessionIdentity
        ):
            raise TypeError("session must be AdbTransportListSessionIdentity or None")
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
        if self.status is AdbTransportListStateStatus.CURRENT:
            if self.identity is None:
                raise ValueError("current transport-list state must have identity and data")
            if self.session is None:
                raise ValueError("current transport-list state must have an owning session")

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
    """Evidence that a raw observation lost its session/list/status authority fence."""

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
AdbTransportListCoordinatedObservationResult: TypeAlias = AdbTransportListObservationResult


@dataclass(frozen=True, slots=True)
class AdbTransportListSessionBegun:
    """Evidence that a fresh producer session acquired transport-list authority."""

    basis: AdbTransportListObservationBasis
    superseded_session: AdbTransportListSessionIdentity | None
    invalidated_identity: AdbTransportListIdentity | None

    def __post_init__(self) -> None:
        if not isinstance(self.basis, AdbTransportListObservationBasis):
            raise TypeError("basis must be AdbTransportListObservationBasis")
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

    @property
    def session(self) -> AdbTransportListSessionIdentity:
        return self.basis.session

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
    """Evidence that a stale session tried to revoke another producer's authority."""

    session: AdbTransportListSessionIdentity
    state: AdbTransportListState

    def __post_init__(self) -> None:
        if not isinstance(self.session, AdbTransportListSessionIdentity):
            raise TypeError("session must be AdbTransportListSessionIdentity")
        if not isinstance(self.state, AdbTransportListState):
            raise TypeError("state must be AdbTransportListState")

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
    """Read the authoritative transport-list observation and producer session."""

    @property
    def session(self) -> AdbTransportListSessionIdentity | None: ...

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
    """Narrow authority for session-fenced transport-list observation transitions."""

    def snapshot(self) -> AdbTransportListState: ...

    def begin_session(self) -> AdbTransportListSessionBegun | None: ...

    def capture_update_basis(
        self,
        session: AdbTransportListSessionIdentity,
    ) -> AdbTransportListObservationBasis | None: ...

    def revoke_session(
        self,
        session: AdbTransportListSessionIdentity,
    ) -> AdbTransportListSessionRevocationResult: ...

    def observe_initial(
        self,
        basis: AdbTransportListObservationBasis,
        transport_list: AdbTransportList,
        expected: AdbTransportListState,
    ) -> AdbTransportListObservationResult: ...

    def observe_update(
        self,
        basis: AdbTransportListObservationBasis,
        transport_list: AdbTransportList,
        expected: AdbTransportListState,
    ) -> AdbTransportListObservationResult: ...


@runtime_checkable
class AdbTransportListStateWriter(AdbTransportListSessionAuthority, Protocol):
    """Apply authoritative session, observation, and invalidation transitions."""

    def invalidate(
        self,
        expected: AdbTransportListIdentity,
    ) -> AdbTransportListInvalidationResult: ...


class AdbTransportListStateStore(AdbTransportListStateView, AdbTransportListStateWriter):
    """Thread-safe authority for session admission, ownership, and observations."""

    def __init__(
        self,
        session_identity_issuer: AdbTransportListSessionIdentityIssuer,
        initial: AdbTransportListState | None = None,
    ) -> None:
        if not isinstance(session_identity_issuer, AdbTransportListSessionIdentityIssuer):
            raise TypeError(
                "session_identity_issuer must be AdbTransportListSessionIdentityIssuer"
            )
        if initial is None:
            state = AdbTransportListState()
        elif isinstance(initial, AdbTransportListState):
            state = initial
        else:
            raise TypeError("initial must be AdbTransportListState or None")
        if state.session is not None:
            raise ValueError("initial transport-list state cannot contain an active session")
        self._lock = Lock()
        self._state = state
        self._identity_issuer = AdbTransportListIdentityIssuer(after=state.identity)
        self._session_identity_issuer = session_identity_issuer
        self._session_admission_open = False

    @property
    def state(self) -> AdbTransportListState:
        with self._lock:
            return self._state

    @property
    def session(self) -> AdbTransportListSessionIdentity | None:
        return self.state.session

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

    @property
    def session_admission_open(self) -> bool:
        """Whether a producer session may currently be created."""

        with self._lock:
            return self._session_admission_open

    def open_session_admission(self) -> None:
        """Admit producer-session creation for the active server lifetime."""

        with self._lock:
            if self._session_admission_open:
                raise RuntimeError("transport-list session admission is already open")
            self._session_admission_open = True

    def close_session_admission(self) -> AdbTransportListSessionRevoked | None:
        """Fence new sessions and revoke the current producer in one state transition."""

        with self._lock:
            self._session_admission_open = False
            return self._revoke_current_session_locked()

    def begin_session(self) -> AdbTransportListSessionBegun | None:
        """Issue and install a fresh session while server admission remains open."""

        with self._lock:
            if not self._session_admission_open:
                return None
            session = self._session_identity_issuer.issue()
            current = self._state
            self._state = AdbTransportListState(
                identity=current.identity,
                transport_list=current.transport_list,
                status=AdbTransportListStateStatus.INVALIDATED,
                session=session,
            )
            return AdbTransportListSessionBegun(
                basis=AdbTransportListObservationBasis(
                    session=session,
                    transport_list_identity=current.identity,
                ),
                superseded_session=current.session,
                invalidated_identity=current.current_identity,
            )

    def capture_update_basis(
        self,
        session: AdbTransportListSessionIdentity,
    ) -> AdbTransportListObservationBasis | None:
        if not isinstance(session, AdbTransportListSessionIdentity):
            raise TypeError("session must be AdbTransportListSessionIdentity")

        with self._lock:
            current = self._state
            if (
                current.session is not session
                or current.status is not AdbTransportListStateStatus.CURRENT
            ):
                return None
            return AdbTransportListObservationBasis(
                session=session,
                transport_list_identity=current.identity,
            )

    def revoke_session(
        self,
        session: AdbTransportListSessionIdentity,
    ) -> AdbTransportListSessionRevocationResult:
        if not isinstance(session, AdbTransportListSessionIdentity):
            raise TypeError("session must be AdbTransportListSessionIdentity")

        with self._lock:
            current = self._state
            if current.session is not session:
                return AdbTransportListSessionRevocationStateConflict(session, current)
            invalidated_identity = current.current_identity
            self._state = self._revoked_state(current)
            return AdbTransportListSessionRevoked(session, invalidated_identity)

    def revoke_current_session(self) -> AdbTransportListSessionRevoked | None:
        """Revoke the currently installed producer session, if any."""

        with self._lock:
            return self._revoke_current_session_locked()

    def _revoke_current_session_locked(self) -> AdbTransportListSessionRevoked | None:
        current = self._state
        session = current.session
        if session is None:
            if current.status is not AdbTransportListStateStatus.INVALIDATED:
                raise RuntimeError("transport-list state without a session must be invalidated")
            return None
        invalidated_identity = current.current_identity
        self._state = self._revoked_state(current)
        return AdbTransportListSessionRevoked(session, invalidated_identity)

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
            self._state = self._revoked_state(current)
            return AdbTransportListInvalidated(expected)

    def observe_initial(
        self,
        basis: AdbTransportListObservationBasis,
        transport_list: AdbTransportList,
        expected: AdbTransportListState,
    ) -> AdbTransportListObservationResult:
        """Commit the sole ``INVALIDATED -> CURRENT`` transition for one session."""

        return self._observe(
            basis,
            transport_list,
            expected,
            required_status=AdbTransportListStateStatus.INVALIDATED,
        )

    def observe_update(
        self,
        basis: AdbTransportListObservationBasis,
        transport_list: AdbTransportList,
        expected: AdbTransportListState,
    ) -> AdbTransportListObservationResult:
        """Commit one ``CURRENT -> CURRENT`` continuation for the owning session."""

        return self._observe(
            basis,
            transport_list,
            expected,
            required_status=AdbTransportListStateStatus.CURRENT,
        )

    def _observe(
        self,
        basis: AdbTransportListObservationBasis,
        transport_list: AdbTransportList,
        expected: AdbTransportListState,
        *,
        required_status: AdbTransportListStateStatus,
    ) -> AdbTransportListObservationResult:
        if not isinstance(basis, AdbTransportListObservationBasis):
            raise TypeError("basis must be AdbTransportListObservationBasis")
        if not isinstance(transport_list, AdbTransportList):
            raise TypeError("transport_list must be AdbTransportList")
        if not isinstance(expected, AdbTransportListState):
            raise TypeError("expected must be AdbTransportListState")
        if not isinstance(required_status, AdbTransportListStateStatus):
            raise TypeError("required_status must be AdbTransportListStateStatus")

        with self._lock:
            current = self._state
            if (
                current != expected
                or current.session is not basis.session
                or current.status is not required_status
                or basis.transport_list_identity != current.identity
            ):
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
            self._state = AdbTransportListState(
                identity=identity,
                transport_list=transport_list,
                status=AdbTransportListStateStatus.CURRENT,
                session=basis.session,
            )
            return AdbTransportListObserved(observation)

    @staticmethod
    def _revoked_state(current: AdbTransportListState) -> AdbTransportListState:
        return AdbTransportListState(
            identity=current.identity,
            transport_list=current.transport_list,
            status=AdbTransportListStateStatus.INVALIDATED,
            session=None,
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
