from __future__ import annotations

from collections.abc import Callable
from threading import Lock
from typing import Generic, TypeVar, cast

from adb._managed.adapter import Adapter
from adb._managed.snapshot import Snapshot
from adb._managed.state import Current, Idle, ManagedState
from adb._resource.pool import GLOBAL_RESOURCE_POOL, ResourcePool


GenerationT = TypeVar("GenerationT")
AccessT = TypeVar("AccessT")
RequirementsT = TypeVar("RequirementsT")
ResolvedResourcesT = TypeVar("ResolvedResourcesT")
CapabilityT = TypeVar("CapabilityT")


class ManagedCoordinator(
    Generic[GenerationT, AccessT, RequirementsT, ResolvedResourcesT, CapabilityT]
):
    """Authority coordinator skeleton for the next architecture.

    Intended acquire sequence:
      requirements -> resolve -> optional ResourceAcquisition -> resolve again
      -> pool.bind -> project -> commit Current

    The transition/rollback algorithm is deliberately not implemented in this
    skeleton so result semantics and cleanup scheduling can be reviewed first.
    """

    def __init__(
        self,
        issue_generation: Callable[[], GenerationT],
        adapter: Adapter[AccessT, RequirementsT, ResolvedResourcesT, CapabilityT],
        *,
        resource_pool: ResourcePool = GLOBAL_RESOURCE_POOL,
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
    def resource_pool(self) -> ResourcePool:
        return self._resource_pool

    @property
    def adapter(self) -> Adapter[AccessT, RequirementsT, ResolvedResourcesT, CapabilityT]:
        return self._adapter

    def read(self) -> Snapshot[GenerationT, AccessT, CapabilityT]:
        with self._lock:
            state = self._state
            if isinstance(state, Current):
                return Snapshot(state.generation, state.access, state.capability)
            return Snapshot(state.generation)

    def acquire(
        self,
        expected: GenerationT,
        access: AccessT,
    ) -> Snapshot[GenerationT, AccessT, CapabilityT]:
        raise NotImplementedError(
            "next-version skeleton: acquire transition/rollback semantics are not implemented yet"
        )

    def release(
        self,
        expected: GenerationT,
        access: AccessT,
    ) -> Snapshot[GenerationT, AccessT, CapabilityT]:
        raise NotImplementedError(
            "next-version skeleton: release/cleanup scheduling semantics are not implemented yet"
        )


__all__ = ["ManagedCoordinator"]
