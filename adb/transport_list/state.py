from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from threading import Lock
from typing import TYPE_CHECKING, Protocol, TypeAlias, runtime_checkable

from adb.transport_list.identity import (
    AdbTransportListIdentity,
    AdbTransportListIdentityIssuer,
)
from adb.transport_list.model import AdbTransportList
from adb.transport_list.observation import AdbTransportListObservation

if TYPE_CHECKING:
    from adb.transport_list.watch.generation import AdbTransportListWatchGeneration


def _require_generation(value: object) -> AdbTransportListWatchGeneration:
    from adb.transport_list.watch.generation import AdbTransportListWatchGeneration

    if not isinstance(value, AdbTransportListWatchGeneration):
        raise TypeError("generation must be AdbTransportListWatchGeneration")
    return value


class AdbTransportListStateStatus(str, Enum):
    """Visibility status of the authoritative transport-list state."""

    CURRENT = "current"
    INVALIDATED = "invalidated"


@dataclass(frozen=True, slots=True, init=False)
class AdbTransportListState:
    """Immutable authoritative transport-list projection state.

    ``generation`` identifies the watch generation that produced the retained observation.
    Invalidated state may retain generation, identity, and data as historical evidence.
    """

    generation: AdbTransportListWatchGeneration | None = None
    identity: AdbTransportListIdentity | None = None
    transport_list: AdbTransportList | None = None
    status: AdbTransportListStateStatus = AdbTransportListStateStatus.INVALIDATED

    def __init__(
        self,
        generation: AdbTransportListWatchGeneration | None = None,
        identity: AdbTransportListIdentity | None = None,
        transport_list: AdbTransportList | None = None,
        status: AdbTransportListStateStatus | None = None,
    ) -> None:
        if status is None:
            status = (
                AdbTransportListStateStatus.CURRENT
                if generation is not None and identity is not None and transport_list is not None
                else AdbTransportListStateStatus.INVALIDATED
            )
        object.__setattr__(self, "generation", generation)
        object.__setattr__(self, "identity", identity)
        object.__setattr__(self, "transport_list", transport_list)
        object.__setattr__(self, "status", status)
        self.__post_init__()

    def __post_init__(self) -> None:
        if self.generation is not None:
            _require_generation(self.generation)
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

        presence = (
            self.generation is not None,
            self.identity is not None,
            self.transport_list is not None,
        )
        if len(set(presence)) != 1:
            raise ValueError(
                "transport-list generation, identity, and data must be present together"
            )
        if self.status is AdbTransportListStateStatus.CURRENT and self.identity is None:
            raise ValueError(
                "current transport-list state must have generation, identity, and data"
            )

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

    @property
    def current_generation(self) -> AdbTransportListWatchGeneration | None:
        """Return the watch generation that produced the current projection, if visible."""

        return (
            self.generation
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
    def generation(self) -> AdbTransportListWatchGeneration:
        return self.observation.generation

    def __bool__(self) -> bool:
        return True


@dataclass(frozen=True, slots=True)
class AdbTransportListObservationStateConflict:
    """Evidence that an observation lost its watch-generation or projection-state fence."""

    generation: AdbTransportListWatchGeneration
    transport_list: AdbTransportList
    state: AdbTransportListState

    def __post_init__(self) -> None:
        _require_generation(self.generation)
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
    """Evidence that invalidation lost its expected identity or generation fence."""

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

    @property
    def current_generation(self) -> AdbTransportListWatchGeneration | None: ...

    def snapshot(self) -> AdbTransportListState: ...


@runtime_checkable
class AdbTransportListStateWriter(Protocol):
    """Apply authoritative transport-list projection transitions."""

    def invalidate(
        self,
        expected: AdbTransportListIdentity,
    ) -> AdbTransportListInvalidationResult: ...

    def invalidate_generation(
        self,
        expected: AdbTransportListWatchGeneration,
    ) -> AdbTransportListInvalidationResult: ...

    def observe_initial(
        self,
        generation: AdbTransportListWatchGeneration,
        transport_list: AdbTransportList,
    ) -> AdbTransportListState | None: ...

    def observe_update(
        self,
        generation: AdbTransportListWatchGeneration,
        transport_list: AdbTransportList,
    ) -> AdbTransportListState | None: ...


@runtime_checkable
class AdbTransportListStateAuthority(
    AdbTransportListStateView,
    AdbTransportListStateWriter,
    Protocol,
):
    """Atomic authority for the generation-tagged transport-list projection."""


class AdbTransportListStateStore(AdbTransportListStateAuthority):
    """Thread-safe authority for generation-tagged transport-list projection transitions."""

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

    @property
    def current_generation(self) -> AdbTransportListWatchGeneration | None:
        return self.state.current_generation

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

    def invalidate_generation(
        self,
        expected: AdbTransportListWatchGeneration,
    ) -> AdbTransportListInvalidationResult:
        _require_generation(expected)

        with self._lock:
            current = self._state
            if current.current_generation != expected or current.current_identity is None:
                return AdbTransportListInvalidationStateConflict(current)
            identity = current.current_identity
            self._state = self._invalidated_state(current)
            return AdbTransportListInvalidated(identity)

    def observe_initial(
        self,
        generation: AdbTransportListWatchGeneration,
        transport_list: AdbTransportList,
    ) -> AdbTransportListState | None:
        """Commit one ``INVALIDATED -> CURRENT`` projection transition."""

        return self._observe(
            generation,
            transport_list,
            required_status=AdbTransportListStateStatus.INVALIDATED,
        )

    def observe_update(
        self,
        generation: AdbTransportListWatchGeneration,
        transport_list: AdbTransportList,
    ) -> AdbTransportListState | None:
        """Commit one same-generation ``CURRENT -> CURRENT`` projection transition."""

        return self._observe(
            generation,
            transport_list,
            required_status=AdbTransportListStateStatus.CURRENT,
        )

    def _observe(
        self,
        generation: AdbTransportListWatchGeneration,
        transport_list: AdbTransportList,
        *,
        required_status: AdbTransportListStateStatus,
    ) -> AdbTransportListState | None:
        _require_generation(generation)
        if not isinstance(transport_list, AdbTransportList):
            raise TypeError("transport_list must be AdbTransportList")
        if not isinstance(required_status, AdbTransportListStateStatus):
            raise TypeError("required_status must be AdbTransportListStateStatus")

        with self._lock:
            current = self._state
            if current.status is not required_status:
                return None
            if (
                required_status is AdbTransportListStateStatus.CURRENT
                and current.current_generation != generation
            ):
                return None

            identity = self._identity_issuer.issue()
            next_state = AdbTransportListState(
                generation=generation,
                identity=identity,
                transport_list=transport_list,
                status=AdbTransportListStateStatus.CURRENT,
            )
            self._state = next_state
            return next_state

    @staticmethod
    def _invalidated_state(current: AdbTransportListState) -> AdbTransportListState:
        return AdbTransportListState(
            generation=current.generation,
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
    "AdbTransportListState",
    "AdbTransportListStateAuthority",
    "AdbTransportListStateStatus",
    "AdbTransportListStateStore",
    "AdbTransportListStateView",
    "AdbTransportListStateWriter",
]
