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
from adb._resource.acquisition import ResourceAcquisition
from adb._resource.pool import GLOBAL_RESOURCE_POOL, ResourcePool, ResourceReservation


GenerationT = TypeVar("GenerationT")
AccessT = TypeVar("AccessT")
RequirementsT = TypeVar("RequirementsT")
ResourceSetT = TypeVar("ResourceSetT")
CapabilityT = TypeVar("CapabilityT")


class ManagedCoordinator(
    Generic[GenerationT, AccessT, RequirementsT, ResourceSetT, CapabilityT]
):
    """Coordinate Access authority around one Access-keyed ResourceSet pool.

    Simplified acquire sequence::

        requirements -> reserve Access -> acquire ResourceSet -> project Capability
        -> atomically install ResourceSet + commit Current

    No ResourceSet composition or cross-Access sharing exists in this model.
    Physical acquisition happens outside the coordinator lock. ``release`` can
    therefore revoke a Preparing attempt, advance authority immediately, and let
    the losing acquire call drain/cleanup its ResourceSet when it returns.
    """

    def __init__(
        self,
        issue_generation: Callable[[], GenerationT],
        adapter: Adapter[AccessT, RequirementsT, ResourceSetT, CapabilityT],
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
        self._state: ManagedState[GenerationT, AccessT, CapabilityT] = Idle(
            issue_generation()
        )

    @property
    def resource_pool(self) -> ResourcePool[AccessT, ResourceSetT]:
        return self._resource_pool

    @property
    def adapter(self) -> Adapter[AccessT, RequirementsT, ResourceSetT, CapabilityT]:
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

        acquisition = ResourceAcquisition()
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
                acquisition=acquisition,
            )
            self._state = Preparing(state.generation, access, attempt)

        reservation: ResourceReservation[AccessT] | None = None
        resources: ResourceSetT | None = None
        resources_obtained = False

        try:
            requirements = self._adapter.access_model.requirements(access)

            if acquisition.revoked:
                return self._finish_superseded(attempt)

            reservation = self._resource_pool.reserve(access)
            if reservation is None:
                abandoned, current_generation = self._abandon_if_current(attempt)
                if abandoned:
                    return AcquireBusy()
                return AcquireSuperseded(current_generation)

            if acquisition.revoked:
                self._resource_pool.cancel(reservation)
                reservation = None
                return self._finish_superseded(attempt)

            resources = self._adapter.resource_lifecycle.acquire(requirements, acquisition)
            if resources is None:
                raise TypeError("ResourceLifecycle.acquire() cannot return None")
            resources_obtained = True

            capability = self._adapter.capability_projection.project(access, resources)
            if capability is None:
                raise TypeError("CapabilityProjection.project() cannot return None")

            with self._lock:
                state = self._state
                owns_authority = (
                    isinstance(state, Preparing)
                    and state.attempt is attempt
                    and not acquisition.revoked
                )
                if owns_authority:
                    self._resource_pool.install(reservation, resources)
                    reservation = None
                    snapshot = Snapshot(state.generation, access, capability)
                    self._state = Current(state.generation, access, capability)
                    return AcquireCommitted(snapshot)
                current_generation = state.generation

            self._retire_and_cleanup_reservation(reservation, access, resources)
            reservation = None
            resources_obtained = False
            return AcquireSuperseded(current_generation)

        except BaseException:
            self._abandon_if_current(attempt)
            if reservation is not None:
                if resources_obtained and resources is not None:
                    self._retire_and_cleanup_reservation(reservation, access, resources)
                else:
                    self._resource_pool.cancel(reservation)
            raise
        finally:
            acquisition.finish()

    def release(
        self,
        expected: GenerationT,
        access: AccessT,
    ) -> ReleaseResult[GenerationT, AccessT]:
        if expected is None:
            raise TypeError("expected cannot be None")
        if access is None:
            raise TypeError("access cannot be None")

        resources: ResourceSetT | None = None
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
                state.attempt.acquisition.revoke()
                self._state = Idle(next_generation)
                return ReleaseAcquisitionRevoked(next_generation)

            if not isinstance(state, Current):
                raise RuntimeError("unsupported Managed state")
            if state.access != access:
                return ReleaseAccessMismatch(state.access)

            next_generation = self._fresh_generation(state.generation)
            resources = self._resource_pool.retire(access)
            self._state = Idle(next_generation)

        # Authority is detached before physical cleanup. On failure the retired
        # record remains in the pool, blocks reacquisition, and can be retried via
        # cleanup_retired().
        assert resources is not None
        assert next_generation is not None
        self._adapter.resource_lifecycle.cleanup(resources)
        self._resource_pool.discard(access, resources)
        return ReleaseDetached(next_generation)

    def cleanup_retired(self, access: AccessT) -> bool:
        """Retry cleanup of one retired Access record left by a prior failure."""

        if access is None:
            raise TypeError("access cannot be None")
        resources = self._resource_pool.retired(access)
        if resources is None:
            return False
        self._adapter.resource_lifecycle.cleanup(resources)
        self._resource_pool.discard(access, resources)
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

    def _retire_and_cleanup_reservation(
        self,
        reservation: ResourceReservation[AccessT],
        access: AccessT,
        resources: ResourceSetT,
    ) -> None:
        self._resource_pool.install(reservation, resources, retired=True)
        self._adapter.resource_lifecycle.cleanup(resources)
        self._resource_pool.discard(access, resources)


__all__ = ["ManagedCoordinator"]
