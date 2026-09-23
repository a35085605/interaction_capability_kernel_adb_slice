from __future__ import annotations

from dataclasses import dataclass
from typing import Generic, TypeAlias, TypeVar

from lifecycle.capability.snapshot import LifecycleSnapshot


GenerationT = TypeVar("GenerationT")
RequestT = TypeVar("RequestT")
CapabilityT = TypeVar("CapabilityT")


@dataclass(frozen=True, slots=True)
class LifecycleResult(Generic[GenerationT, RequestT, CapabilityT]):
    """Return the snapshot decided or committed by one lifecycle call.

    ``execution_started`` is true exactly when this call passed lifecycle fencing and
    ownership checks, took responsibility for the operation, and entered ACQUIRING or
    RELEASING. It does not claim that an operating-system side effect occurred.
    """

    snapshot: LifecycleSnapshot[GenerationT, RequestT, CapabilityT]
    execution_started: bool

    def __post_init__(self) -> None:
        if not isinstance(self.snapshot, LifecycleSnapshot):
            raise TypeError("snapshot must be LifecycleSnapshot")
        if not isinstance(self.execution_started, bool):
            raise TypeError("execution_started must be a bool")


AcquireResult: TypeAlias = LifecycleResult[GenerationT, RequestT, CapabilityT]
ReleaseResult: TypeAlias = LifecycleResult[GenerationT, RequestT, CapabilityT]


__all__ = ["AcquireResult", "LifecycleResult", "ReleaseResult"]
