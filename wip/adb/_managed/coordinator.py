from __future__ import annotations

from collections.abc import Callable
from threading import Lock
from typing import Generic, TypeVar

from adb._managed.adapter import Adapter
from adb._managed.result import (
    AcquireAccessMismatch,
    AcquireBusy,
    AcquireCommitted,
    AcquireExisting,
    AcquireResult,
    AcquireSuperseded,
    GenerationMismatch,
    ReleaseAccessMismatch,
    ReleaseAcquisitionRevoked,
    ReleaseDetached,
    ReleaseInactive,
    ReleaseResult,
)
from adb._managed.snapshot import Snapshot
from adb._managed.state import Current, Idle, ManagedAttempt, ManagedState, Preparing
from adb._managed.pool import (
    GLOBAL_RESOURCE_POOL,
    ResourceLease,
    ResourcePool,
    ResourceReservation,
)


GenerationT = TypeVar("GenerationT")
AccessT = TypeVar("AccessT")
ResourceSetT = TypeVar("ResourceSetT")
CapabilityT = TypeVar("CapabilityT")


class ManagedCoordinator(
    Generic[GenerationT, AccessT, ResourceSetT, CapabilityT]
):
    """Coordinate Access authority around policy-aware physical ResourceSets.

    Simplified acquire sequence::

        ResourceRequirement -> reserve/reuse -> acquire if needed -> Capability
        -> atomically install/retain lease + commit Current

    EXCLUSIVE, SHARED, and PARALLEL coexistence is decided by ``ResourcePool``
    before physical acquisition. Physical acquisition happens outside the
    coordinator lock. ``release`` can therefore revoke a Preparing attempt,
    advance authority immediately, and let the losing acquire call drain/cleanup
    its lease or ResourceSet when it returns.
    """

    def __init__(
        self,
        issue_generation: Callable[[], GenerationT],
        adapter: Adapter[AccessT, ResourceSetT, CapabilityT],
        *,
        resource_pool: ResourcePool[AccessT, ResourceSetT] = GLOBAL_RESOURCE_POOL,
    ) -> None:
        if not callable(issue_generation):
            raise TypeError("issue_generation must be callable")
        if not isinstance(resource_pool, ResourcePool):
            raise TypeError("resource_pool must be ResourcePool")
        self._issue_generation = issue_generation
        self._adapter = adapter
        self._resource_pool = resource_pool
        self._lock = Lock()
        self._state: ManagedState[
            GenerationT, AccessT, ResourceSetT, CapabilityT
        ] = Idle(issue_generation())

    @property
    def resource_pool(self) -> ResourcePool[AccessT, ResourceSetT]:
        return self._resource_pool

    @property
    def adapter(self) -> Adapter[AccessT, ResourceSetT, CapabilityT]:
        return self._adapter

    def read(self) -> Snapshot[GenerationT, AccessT, CapabilityT]:
        with self._lock:
            return self._snapshot_locked()

    def acquire(
        self,
        expected: GenerationT,
        access: AccessT,
    ) -> AcquireResult[GenerationT, AccessT, CapabilityT]:
        if expected is None:
            raise TypeError("expected cannot be None")
        if access is None:
            raise TypeError("access cannot be None")

        with self._lock:
            state = self._state
            if expected != state.generation:
                return GenerationMismatch(state.generation)
            if isinstance(state, Current):
                snapshot = self._snapshot_locked()
                if state.access == access:
                    return AcquireExisting(snapshot)
                return AcquireAccessMismatch(state.access)
            if isinstance(state, Preparing):
                return AcquireBusy()
            if not isinstance(state, Idle):
                raise RuntimeError("unsupported Managed state")

            attempt = ManagedAttempt(
                generation=state.generation,
                access=access,
            )
            self._state = Preparing(state.generation, access, attempt)

        reservation: ResourceReservation[AccessT] | None = None
        lease: ResourceLease[AccessT, ResourceSetT] | None = None
        resources: ResourceSetT | None = None
        resources_obtained = False

        try:
            requirement = self._adapter.access_model.requirements(access)

            if attempt.revoked:
                return self._finish_superseded(attempt)

            claim = self._resource_pool.reserve(access, requirement)
            if claim is None:
                abandoned, current_generation = self._abandon_if_current(attempt)
                if abandoned:
                    return AcquireBusy()
                return AcquireSuperseded(current_generation)

            if isinstance(claim, ResourceLease):
                lease = claim
                resources = claim.resources
            else:
                reservation = claim

            if attempt.revoked:
                if lease is not None:
                    self._release_lease_and_cleanup(lease)
                    lease = None
                elif reservation is not None:
                    self._resource_pool.cancel(reservation)
                    reservation = None
                return self._finish_superseded(attempt)

            if resources is None:
                assert reservation is not None
                resources = self._adapter.resource_lifecycle.acquire(
                    access,
                )
                if resources is None:
                    raise TypeError("AccessResourceLifecycle.acquire() cannot return None")
                resources_obtained = True

            capability = self._adapter.capability_projection.project(access, resources)
            if capability is None:
                raise TypeError("CapabilityProjection.project() cannot return None")

            with self._lock:
                state = self._state
                owns_authority = (
                    isinstance(state, Preparing)
                    and state.attempt is attempt
                    and not attempt.revoked
                )
                if owns_authority:
                    if lease is None:
                        assert reservation is not None
                        lease = self._resource_pool.install(reservation, resources)
                        reservation = None
                    snapshot = Snapshot(state.generation, access, capability)
                    self._state = Current(
                        state.generation,
                        access,
                        capability,
                        lease,
                    )
                    return AcquireCommitted(snapshot)
                current_generation = state.generation

            if lease is not None:
                self._release_lease_and_cleanup(lease)
                lease = None
            elif reservation is not None:
                if resources_obtained:
                    self._install_retire_and_cleanup_reservation(reservation, resources)
                    resources_obtained = False
                else:
                    self._resource_pool.cancel(reservation)
                reservation = None
            return AcquireSuperseded(current_generation)

        except BaseException:
            self._abandon_if_current(attempt)
            if lease is not None:
                self._release_lease_and_cleanup(lease)
            elif reservation is not None:
                if resources_obtained and resources is not None:
                    self._install_retire_and_cleanup_reservation(reservation, resources)
                else:
                    self._resource_pool.cancel(reservation)
            raise
        finally:
            attempt.finish()

    def release(
        self,
        expected: GenerationT,
        access: AccessT,
    ) -> ReleaseResult[GenerationT, AccessT]:
        if expected is None:
            raise TypeError("expected cannot be None")
        if access is None:
            raise TypeError("access cannot be None")

        retired = None
        next_generation: GenerationT | None = None

        with self._lock:
            state = self._state
            if expected != state.generation:
                return GenerationMismatch(state.generation)
            if isinstance(state, Idle):
                return ReleaseInactive()

            if isinstance(state, Preparing):
                if state.access != access:
                    return ReleaseAccessMismatch(state.access)
                next_generation = self._fresh_generation(state.generation)
                state.attempt.revoke()
                self._state = Idle(next_generation)
                return ReleaseAcquisitionRevoked(next_generation)

            if not isinstance(state, Current):
                raise RuntimeError("unsupported Managed state")
            if state.access != access:
                return ReleaseAccessMismatch(state.access)

            next_generation = self._fresh_generation(state.generation)
            retired = self._resource_pool.retire(state.resource_lease)
            self._state = Idle(next_generation)

        # Authority is detached before physical cleanup. SHARED resources remain
        # active while another lease exists. If final cleanup fails, the retired
        # record stays in the pool and can be retried via cleanup_retired().
        assert next_generation is not None
        if retired is not None:
            self._adapter.resource_lifecycle.cleanup(retired.resources)
            self._resource_pool.discard(retired)
        return ReleaseDetached(next_generation)

    def cleanup_retired(self, access: AccessT) -> bool:
        """Retry cleanup of every retired record currently retained for Access."""

        if access is None:
            raise TypeError("access cannot be None")
        requirement = self._adapter.access_model.requirements(access)
        retired_records = self._resource_pool.retired(access, requirement.key)
        if not retired_records:
            return False
        for retired in retired_records:
            self._adapter.resource_lifecycle.cleanup(retired.resources)
            self._resource_pool.discard(retired)
        return True

    def _snapshot_locked(self) -> Snapshot[GenerationT, AccessT, CapabilityT]:
        state = self._state
        if isinstance(state, Current):
            return Snapshot(state.generation, state.access, state.capability)
        return Snapshot(state.generation)

    def _fresh_generation(self, previous: GenerationT) -> GenerationT:
        next_generation = self._issue_generation()
        if next_generation == previous:
            raise RuntimeError("issue_generation must return a fresh generation")
        return next_generation

    def _abandon_if_current(
        self,
        attempt: ManagedAttempt[GenerationT, AccessT],
    ) -> tuple[bool, GenerationT]:
        with self._lock:
            state = self._state
            if isinstance(state, Preparing) and state.attempt is attempt:
                self._state = Idle(state.generation)
                return True, state.generation
            return False, state.generation

    def _finish_superseded(
        self,
        attempt: ManagedAttempt[GenerationT, AccessT],
    ) -> AcquireSuperseded[GenerationT]:
        _abandoned, current_generation = self._abandon_if_current(attempt)
        return AcquireSuperseded(current_generation)

    def _install_retire_and_cleanup_reservation(
        self,
        reservation: ResourceReservation[AccessT],
        resources: ResourceSetT,
    ) -> None:
        lease = self._resource_pool.install(reservation, resources)
        self._release_lease_and_cleanup(lease)

    def _release_lease_and_cleanup(
        self,
        lease: ResourceLease[AccessT, ResourceSetT],
    ) -> None:
        retired = self._resource_pool.retire(lease)
        if retired is None:
            return
        self._adapter.resource_lifecycle.cleanup(retired.resources)
        self._resource_pool.discard(retired)


__all__ = ["ManagedCoordinator"]
