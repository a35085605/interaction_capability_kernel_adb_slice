from __future__ import annotations

from dataclasses import dataclass, field
from threading import Lock
from typing import Any, Generic, Hashable, TypeVar

from adb._managed.requirement import ResourcePolicy, ResourceRequirement


AccessT = TypeVar("AccessT")
ResourceSetT = TypeVar("ResourceSetT")


@dataclass(frozen=True, slots=True)
class ResourceRecord(Generic[AccessT, ResourceSetT]):
    """Immutable point-in-time view of one retained physical ResourceSet."""

    access: AccessT
    requirement_key: Hashable
    policy: ResourcePolicy
    resources: ResourceSetT
    leases: int
    retired: bool = False


@dataclass(frozen=True, slots=True, eq=False)
class ResourceReservation(Generic[AccessT]):
    """Identity token reserving one new physical ResourceSet acquisition."""

    access: AccessT
    requirement_key: Hashable
    policy: ResourcePolicy


@dataclass(frozen=True, slots=True, eq=False)
class ResourceLease(Generic[AccessT, ResourceSetT]):
    """Identity token retaining one installed ResourceSet for a coordinator."""

    access: AccessT
    requirement_key: Hashable
    resources: ResourceSetT
    _record_token: object
    _lease_token: object


@dataclass(frozen=True, slots=True, eq=False)
class RetiredResource(Generic[AccessT, ResourceSetT]):
    """Exact retired record that remains retained until cleanup succeeds."""

    access: AccessT
    requirement_key: Hashable
    resources: ResourceSetT
    _record_token: object


@dataclass(slots=True)
class _RecordState(Generic[AccessT, ResourceSetT]):
    access: AccessT
    requirement_key: Hashable
    policy: ResourcePolicy
    resources: ResourceSetT
    token: object
    lease_tokens: set[object] = field(default_factory=set)
    retired: bool = False


class ResourcePool(Generic[AccessT, ResourceSetT]):
    """Process-wide coordinator registry for ResourceSet coexistence and retention.

    EXCLUSIVE requirements block every retained or reserved ResourceSet for the same
    Access. SHARED requirements reuse one active ResourceSet with the same key and
    reference-count its leases. PARALLEL requirements always acquire an independent
    ResourceSet. A conflicting EXCLUSIVE record, including a retired one awaiting
    cleanup, blocks non-exclusive acquisition.
    """

    def __init__(self) -> None:
        self._lock = Lock()
        self._records: list[_RecordState[AccessT, ResourceSetT]] = []
        self._reservations: list[ResourceReservation[AccessT]] = []

    @staticmethod
    def _validate_access(access: AccessT) -> None:
        if access is None:
            raise TypeError("access cannot be None")
        try:
            hash(access)
        except TypeError as exc:
            raise TypeError("access must be hashable") from exc

    @staticmethod
    def _validate_requirement(requirement: ResourceRequirement) -> None:
        if not isinstance(requirement, ResourceRequirement):
            raise TypeError("requirement must be ResourceRequirement")

    def snapshot(self) -> tuple[ResourceRecord[AccessT, ResourceSetT], ...]:
        """Return immutable views of every retained physical ResourceSet."""

        with self._lock:
            return tuple(
                ResourceRecord(
                    state.access,
                    state.requirement_key,
                    state.policy,
                    state.resources,
                    len(state.lease_tokens),
                    state.retired,
                )
                for state in self._records
            )

    def lookup(
        self,
        access: AccessT,
        requirement_key: Hashable,
    ) -> tuple[ResourceSetT, ...]:
        """Return all active ResourceSets matching one Access + requirement key."""

        self._validate_access(access)
        if requirement_key is None:
            raise TypeError("requirement_key cannot be None")
        with self._lock:
            return tuple(
                state.resources
                for state in self._records
                if state.access == access
                and state.requirement_key == requirement_key
                and not state.retired
            )

    def retired(
        self,
        access: AccessT,
        requirement_key: Hashable,
    ) -> tuple[RetiredResource[AccessT, ResourceSetT], ...]:
        """Return retired records matching one Access + requirement key."""

        self._validate_access(access)
        if requirement_key is None:
            raise TypeError("requirement_key cannot be None")
        with self._lock:
            return tuple(
                RetiredResource(
                    state.access,
                    state.requirement_key,
                    state.resources,
                    state.token,
                )
                for state in self._records
                if state.access == access
                and state.requirement_key == requirement_key
                and state.retired
            )

    def reserve(
        self,
        access: AccessT,
        requirement: ResourceRequirement,
    ) -> ResourceReservation[AccessT] | ResourceLease[AccessT, ResourceSetT] | None:
        """Reserve/acquire one requirement according to its same-Access policy.

        SHARED may return an already-installed lease. Returning ``None`` means the
        request conflicts with a retained/reserved resource or a same-key SHARED
        acquisition is still being prepared.
        """

        self._validate_access(access)
        self._validate_requirement(requirement)

        with self._lock:
            same_access_records = [
                state for state in self._records if state.access == access
            ]
            same_access_reservations = [
                reservation
                for reservation in self._reservations
                if reservation.access == access
            ]

            if requirement.policy is ResourcePolicy.EXCLUSIVE:
                if same_access_records or same_access_reservations:
                    return None
                return self._reserve_locked(access, requirement)

            if any(
                state.policy is ResourcePolicy.EXCLUSIVE
                for state in same_access_records
            ) or any(
                reservation.policy is ResourcePolicy.EXCLUSIVE
                for reservation in same_access_reservations
            ):
                return None

            same_key_records = [
                state
                for state in same_access_records
                if state.requirement_key == requirement.key
            ]
            same_key_reservations = [
                reservation
                for reservation in same_access_reservations
                if reservation.requirement_key == requirement.key
            ]

            # One resource identity must not change coexistence semantics while any
            # record/reservation for that identity still exists.
            if any(state.policy is not requirement.policy for state in same_key_records):
                return None
            if any(
                reservation.policy is not requirement.policy
                for reservation in same_key_reservations
            ):
                return None

            if requirement.policy is ResourcePolicy.SHARED:
                if any(state.retired for state in same_key_records):
                    return None
                for state in same_key_records:
                    if not state.retired:
                        return self._lease_locked(state)
                if same_key_reservations:
                    return None

            return self._reserve_locked(access, requirement)

    def _reserve_locked(
        self,
        access: AccessT,
        requirement: ResourceRequirement,
    ) -> ResourceReservation[AccessT]:
        reservation = ResourceReservation(access, requirement.key, requirement.policy)
        self._reservations.append(reservation)
        return reservation

    def cancel(self, reservation: ResourceReservation[AccessT]) -> bool:
        """Cancel one still-current reservation; return whether it was removed."""

        if not isinstance(reservation, ResourceReservation):
            raise TypeError("reservation must be ResourceReservation")
        with self._lock:
            for index, current in enumerate(self._reservations):
                if current is reservation:
                    del self._reservations[index]
                    return True
            return False

    def install(
        self,
        reservation: ResourceReservation[AccessT],
        resources: ResourceSetT,
    ) -> ResourceLease[AccessT, ResourceSetT]:
        """Consume a reservation, install its ResourceSet, and return the first lease."""

        if not isinstance(reservation, ResourceReservation):
            raise TypeError("reservation must be ResourceReservation")
        if resources is None:
            raise TypeError("resources cannot be None")

        with self._lock:
            reservation_index = next(
                (
                    index
                    for index, current in enumerate(self._reservations)
                    if current is reservation
                ),
                None,
            )
            if reservation_index is None:
                raise RuntimeError("resource reservation is not current")

            token = object()
            state = _RecordState(
                access=reservation.access,
                requirement_key=reservation.requirement_key,
                policy=reservation.policy,
                resources=resources,
                token=token,
            )
            self._records.append(state)
            del self._reservations[reservation_index]
            return self._lease_locked(state)

    @staticmethod
    def _lease_locked(
        state: _RecordState[AccessT, ResourceSetT],
    ) -> ResourceLease[AccessT, ResourceSetT]:
        lease_token = object()
        state.lease_tokens.add(lease_token)
        return ResourceLease(
            state.access,
            state.requirement_key,
            state.resources,
            state.token,
            lease_token,
        )

    def retire(
        self,
        lease: ResourceLease[AccessT, ResourceSetT],
    ) -> RetiredResource[AccessT, ResourceSetT] | None:
        """Release one lease; retire the ResourceSet when its last lease detaches."""

        if not isinstance(lease, ResourceLease):
            raise TypeError("lease must be ResourceLease")

        with self._lock:
            state = next(
                (state for state in self._records if state.token is lease._record_token),
                None,
            )
            if state is None:
                raise RuntimeError("resource lease is not retained")
            if state.retired:
                raise RuntimeError("resource lease is already retired")
            if state.resources is not lease.resources:
                raise RuntimeError("resource lease does not match retained resources")
            if lease._lease_token not in state.lease_tokens:
                raise RuntimeError("resource lease is not current")

            state.lease_tokens.remove(lease._lease_token)
            if state.lease_tokens:
                return None

            state.retired = True
            return RetiredResource(
                state.access,
                state.requirement_key,
                state.resources,
                state.token,
            )

    def discard(self, retired: RetiredResource[AccessT, ResourceSetT]) -> None:
        """Forget one exact retired record after physical cleanup succeeds."""

        if not isinstance(retired, RetiredResource):
            raise TypeError("retired must be RetiredResource")

        with self._lock:
            record_index = next(
                (
                    index
                    for index, state in enumerate(self._records)
                    if state.token is retired._record_token
                ),
                None,
            )
            if record_index is None:
                raise RuntimeError("retired resource is not retained")
            state = self._records[record_index]
            if state.resources is not retired.resources:
                raise RuntimeError("retired resource does not match retained resources")
            if not state.retired:
                raise RuntimeError("resource set must be retired before discard")
            del self._records[record_index]


GLOBAL_RESOURCE_POOL: ResourcePool[Any, Any] = ResourcePool()


__all__ = [
    "GLOBAL_RESOURCE_POOL",
    "ResourceLease",
    "ResourcePool",
    "ResourceRecord",
    "ResourceReservation",
    "RetiredResource",
]
