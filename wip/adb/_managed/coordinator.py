from __future__ import annotations

from collections.abc import Callable
from threading import Lock
from typing import Generic, TypeVar

from adb._managed.adapter import Adapter
from adb._managed.pool import (
    GLOBAL_RESOURCE_POOL,
    ResourceLease,
    ResourcePool,
    ResourceRequest,
    RetiredResource,
)
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
from adb._resource.lifecycle import ResourceAcquisitionRequest, ResourceSet


GenerationT = TypeVar("GenerationT")
AccessT = TypeVar("AccessT")
ResourceT = TypeVar("ResourceT")
CapabilityT = TypeVar("CapabilityT")


class _PoolAcquisitionRequest(Generic[AccessT, ResourceT]):
    """Narrow lifecycle-facing view backed by one Pool ResourceRequest."""

    def __init__(
        self,
        pool: ResourcePool[AccessT, ResourceT],
        request: ResourceRequest[AccessT],
    ) -> None:
        self._pool = pool
        self._request = request

    @property
    def request_id(self):
        return self._request.request_id

    @property
    def interrupted(self) -> bool:
        return self._pool.is_interrupted(self._request)

    def publish(self, resources: ResourceSet[ResourceT]) -> None:
        self._pool.publish(self._request, resources)


class ManagedCoordinator(
    Generic[GenerationT, AccessT, ResourceT, CapabilityT]
):
    """Coordinate Access authority around policy-aware physical ResourceSets.

    Authority cancellation and physical request interruption are independent.
    ``ManagedAttempt.revoke`` still removes capability commit authority
    immediately. A new physical acquisition also owns a Pool ``ResourceRequest``
    with a Request ID. Releasing a Preparing coordinator marks that request
    interrupted and asks the lifecycle to abort I/O, but cleanup is delayed until
    the producer's final ``finish`` changes ``processing`` to false.

    Simplified new-resource acquire sequence::

        ResourceRequirement -> processing ResourceRequest -> acquire/publish
        -> Capability -> atomically finish/install lease + commit Current

    SHARED reuse may skip physical acquisition and return an existing lease.
    """

    def __init__(
        self,
        issue_generation: Callable[[], GenerationT],
        adapter: Adapter[AccessT, ResourceT, CapabilityT],
        *,
        resource_pool: ResourcePool[AccessT, ResourceT] = GLOBAL_RESOURCE_POOL,
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
            GenerationT, AccessT, ResourceT, CapabilityT
        ] = Idle(issue_generation())

    @property
    def resource_pool(self) -> ResourcePool[AccessT, ResourceT]:
        return self._resource_pool

    @property
    def adapter(self) -> Adapter[AccessT, ResourceT, CapabilityT]:
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

        request: ResourceRequest[AccessT] | None = None
        request_context: ResourceAcquisitionRequest[ResourceT] | None = None
        lease: ResourceLease[AccessT, ResourceT] | None = None
        resources: ResourceSet[ResourceT] | None = None

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
                request = claim
                request_context = _PoolAcquisitionRequest(self._resource_pool, request)

                # Publish the request handle into Preparing so release() can mark
                # physical interruption. If authority was revoked between reserve
                # and this handoff, no producer has started and the empty request
                # can be cancelled safely.
                with self._lock:
                    state = self._state
                    owns_preparing = (
                        isinstance(state, Preparing)
                        and state.attempt is attempt
                        and not attempt.revoked
                    )
                    if owns_preparing:
                        self._state = Preparing(
                            state.generation,
                            state.access,
                            state.attempt,
                            request,
                        )
                    current_generation = state.generation

                if not owns_preparing:
                    self._resource_pool.cancel(request)
                    request = None
                    return AcquireSuperseded(current_generation)

            if attempt.revoked:
                if lease is not None:
                    self._release_lease_and_cleanup(lease)
                    lease = None
                elif request is not None:
                    # No lifecycle producer has started yet.
                    self._resource_pool.cancel(request)
                    request = None
                return self._finish_superseded(attempt)

            if resources is None:
                assert request is not None
                assert request_context is not None
                resources = self._adapter.resource_lifecycle.acquire(
                    access,
                    request_context,
                )
                if resources is None:
                    raise TypeError("AccessResourceLifecycle.acquire() cannot return None")

                # Keep processing=true through capability projection. This final
                # acquire snapshot is visible to Pool interruption immediately,
                # but cleanup cannot begin while the coordinator may still use it.
                self._resource_pool.publish(request, resources)

                if attempt.revoked:
                    current_generation = self._current_generation()
                    self._interrupt_finish_request_and_cleanup(request, resources)
                    request = None
                    return AcquireSuperseded(current_generation)

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
                        assert request is not None
                        assert resources is not None
                        retired = self._resource_pool.finish(request, resources)
                        if retired is not None:
                            raise RuntimeError(
                                "current Managed authority has an interrupted resource request"
                            )
                        lease = self._resource_pool.install(request)
                        request = None
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
            elif request is not None:
                self._interrupt_finish_request_and_cleanup(request, resources)
                request = None
            return AcquireSuperseded(current_generation)

        except BaseException:
            self._abandon_if_current(attempt)
            if lease is not None:
                self._release_lease_and_cleanup(lease)
            elif request is not None:
                self._interrupt_finish_request_and_cleanup(request, resources)
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

        retired: RetiredResource[AccessT, ResourceT] | None = None
        request: ResourceRequest[AccessT] | None = None
        next_generation: GenerationT | None = None
        revoked_acquisition = False

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
                request = state.request
                self._state = Idle(next_generation)
                revoked_acquisition = True
            else:
                if not isinstance(state, Current):
                    raise RuntimeError("unsupported Managed state")
                if state.access != access:
                    return ReleaseAccessMismatch(state.access)

                next_generation = self._fresh_generation(state.generation)
                retired = self._resource_pool.retire(state.resource_lease)
                self._state = Idle(next_generation)

        # Managed authority is already detached here. Physical request interruption
        # is deliberately outside the coordinator lock so a backend abort cannot
        # block reads/releases of authority state.
        assert next_generation is not None
        if revoked_acquisition:
            if request is not None:
                interruption = self._resource_pool.interrupt(request)
                if interruption.processing:
                    request_context = _PoolAcquisitionRequest(
                        self._resource_pool,
                        request,
                    )
                    self._adapter.resource_lifecycle.interrupt(access, request_context)
                elif interruption.retired is not None:
                    self._cleanup_retired(interruption.retired)
            return ReleaseAcquisitionRevoked(next_generation)

        # SHARED resources remain active while another lease exists. If final
        # cleanup fails, the retired record stays in the Pool for retry.
        if retired is not None:
            self._cleanup_retired(retired)
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
            self._cleanup_retired(retired)
        return True

    def _snapshot_locked(self) -> Snapshot[GenerationT, AccessT, CapabilityT]:
        state = self._state
        if isinstance(state, Current):
            return Snapshot(state.generation, state.access, state.capability)
        return Snapshot(state.generation)

    def _current_generation(self) -> GenerationT:
        with self._lock:
            return self._state.generation

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

    def _interrupt_finish_request_and_cleanup(
        self,
        request: ResourceRequest[AccessT],
        resources: ResourceSet[ResourceT] | None,
    ) -> None:
        """Finish a producer that has returned/raised after losing ownership."""

        interruption = self._resource_pool.interrupt(request)
        if interruption.retired is not None:
            self._cleanup_retired(interruption.retired)
            return
        if not interruption.processing:
            return

        retired = self._resource_pool.finish(request, resources)
        if retired is not None:
            self._cleanup_retired(retired)

    def _release_lease_and_cleanup(
        self,
        lease: ResourceLease[AccessT, ResourceT],
    ) -> None:
        retired = self._resource_pool.retire(lease)
        if retired is not None:
            self._cleanup_retired(retired)

    def _cleanup_retired(
        self,
        retired: RetiredResource[AccessT, ResourceT],
    ) -> None:
        self._adapter.resource_lifecycle.cleanup(retired.resources)
        self._resource_pool.discard(retired)


__all__ = ["ManagedCoordinator"]
