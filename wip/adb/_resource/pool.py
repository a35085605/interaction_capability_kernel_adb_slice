from __future__ import annotations

from dataclasses import dataclass
from threading import Lock
from typing import Any, Generic, TypeVar


AccessT = TypeVar("AccessT")
ResourceSetT = TypeVar("ResourceSetT")


@dataclass(frozen=True, slots=True)
class ResourceRecord(Generic[AccessT, ResourceSetT]):
    """Immutable point-in-time view of one Access -> ResourceSet mapping."""

    access: AccessT
    resources: ResourceSetT
    retired: bool = False


@dataclass(frozen=True, slots=True, eq=False)
class ResourceReservation(Generic[AccessT]):
    """Identity token reserving one Access while its ResourceSet is prepared."""

    access: AccessT


@dataclass(slots=True)
class _RecordState(Generic[AccessT, ResourceSetT]):
    access: AccessT
    resources: ResourceSetT
    retired: bool = False


class ResourcePool(Generic[AccessT, ResourceSetT]):
    """Process-wide registry of one ResourceSet per Access.

    The simplified model deliberately does not compose or share resources between
    different Access values. A reservation blocks duplicate physical acquisition
    while a ResourceSet is being prepared. Retired records remain retained until
    physical cleanup succeeds and ``discard`` confirms their removal.
    """

    def __init__(self) -> None:
        self._lock = Lock()
        self._records: dict[AccessT, _RecordState[AccessT, ResourceSetT]] = {}
        self._reservations: dict[AccessT, ResourceReservation[AccessT]] = {}

    @staticmethod
    def _validate_access(access: AccessT) -> None:
        if access is None:
            raise TypeError("access cannot be None")
        try:
            hash(access)
        except TypeError as exc:
            raise TypeError("access must be hashable") from exc

    def snapshot(self) -> tuple[ResourceRecord[AccessT, ResourceSetT], ...]:
        """Return immutable views of every retained Access -> ResourceSet record."""

        with self._lock:
            return tuple(
                ResourceRecord(state.access, state.resources, state.retired)
                for state in self._records.values()
            )

    def lookup(self, access: AccessT) -> ResourceSetT | None:
        """Return the active ResourceSet for ``access``; retired records are hidden."""

        self._validate_access(access)
        with self._lock:
            state = self._records.get(access)
            if state is None or state.retired:
                return None
            return state.resources

    def retired(self, access: AccessT) -> ResourceSetT | None:
        """Return the retained retired ResourceSet for cleanup retry, if any."""

        self._validate_access(access)
        with self._lock:
            state = self._records.get(access)
            if state is None or not state.retired:
                return None
            return state.resources

    def reserve(self, access: AccessT) -> ResourceReservation[AccessT] | None:
        """Reserve an absent Access for acquisition.

        ``None`` means the Access is already retained (active or retired) or another
        acquisition already owns its reservation.
        """

        self._validate_access(access)
        with self._lock:
            if access in self._records or access in self._reservations:
                return None
            reservation = ResourceReservation(access)
            self._reservations[access] = reservation
            return reservation

    def cancel(self, reservation: ResourceReservation[AccessT]) -> bool:
        """Cancel one still-current reservation; return whether it was removed."""

        if not isinstance(reservation, ResourceReservation):
            raise TypeError("reservation must be ResourceReservation")
        with self._lock:
            current = self._reservations.get(reservation.access)
            if current is not reservation:
                return False
            del self._reservations[reservation.access]
            return True

    def install(
        self,
        reservation: ResourceReservation[AccessT],
        resources: ResourceSetT,
        *,
        retired: bool = False,
    ) -> ResourceRecord[AccessT, ResourceSetT]:
        """Consume a reservation and install exactly one ResourceSet for its Access."""

        if not isinstance(reservation, ResourceReservation):
            raise TypeError("reservation must be ResourceReservation")
        if resources is None:
            raise TypeError("resources cannot be None")
        if not isinstance(retired, bool):
            raise TypeError("retired must be bool")

        with self._lock:
            current = self._reservations.get(reservation.access)
            if current is not reservation:
                raise RuntimeError("resource reservation is not current")
            if reservation.access in self._records:
                raise RuntimeError("resource access is already retained")

            state = _RecordState(
                access=reservation.access,
                resources=resources,
                retired=retired,
            )
            self._records[reservation.access] = state
            del self._reservations[reservation.access]
            return ResourceRecord(state.access, state.resources, state.retired)

    def retire(self, access: AccessT) -> ResourceSetT:
        """Retire the retained ResourceSet for ``access`` and return it for cleanup."""

        self._validate_access(access)
        with self._lock:
            state = self._records.get(access)
            if state is None:
                raise RuntimeError("resource access is not retained")
            state.retired = True
            return state.resources

    def discard(self, access: AccessT, resources: ResourceSetT) -> None:
        """Forget a retired record after physical cleanup has succeeded.

        The ResourceSet identity check prevents a stale cleanup completion from
        discarding a later record for the same Access.
        """

        self._validate_access(access)
        if resources is None:
            raise TypeError("resources cannot be None")
        with self._lock:
            state = self._records.get(access)
            if state is None:
                raise RuntimeError("resource access is not retained")
            if state.resources is not resources:
                raise RuntimeError("resource set does not match the retained access")
            if not state.retired:
                raise RuntimeError("resource set must be retired before discard")
            del self._records[access]


GLOBAL_RESOURCE_POOL: ResourcePool[Any, Any] = ResourcePool()


__all__ = [
    "GLOBAL_RESOURCE_POOL",
    "ResourcePool",
    "ResourceRecord",
    "ResourceReservation",
]
