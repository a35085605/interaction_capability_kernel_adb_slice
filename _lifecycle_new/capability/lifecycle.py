from __future__ import annotations

from typing import Protocol, TypeVar, runtime_checkable

from _lifecycle_new.capability.result import AcquireResult, ReleaseResult
from _lifecycle_new.capability.snapshot import LifecycleSnapshot


GenerationT = TypeVar("GenerationT")
RequestT = TypeVar("RequestT")
CapabilityT = TypeVar("CapabilityT")


@runtime_checkable
class CapabilityLifecycle(Protocol[GenerationT, RequestT, CapabilityT]):
    """Read, acquire, and release one generation-scoped capability lifecycle."""

    def read(self) -> LifecycleSnapshot[GenerationT, RequestT, CapabilityT]: ...

    def acquire(
        self,
        expected_generation: GenerationT,
        request: RequestT,
    ) -> AcquireResult[GenerationT, RequestT, CapabilityT]: ...

    def release(
        self,
        expected_generation: GenerationT,
        request: RequestT,
    ) -> ReleaseResult[GenerationT, RequestT, CapabilityT]: ...


__all__ = ["CapabilityLifecycle"]
