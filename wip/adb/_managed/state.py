from __future__ import annotations

from dataclasses import dataclass
from typing import Generic, TypeAlias, TypeVar

from adb._resource.acquisition import ResourceAcquisition
from adb._resource.pool import ResourceLease


GenerationT = TypeVar("GenerationT")
AccessT = TypeVar("AccessT")
ResourceSetT = TypeVar("ResourceSetT")
CapabilityT = TypeVar("CapabilityT")


@dataclass(frozen=True, slots=True, eq=False)
class ManagedAttempt(Generic[GenerationT, AccessT]):
    """Identity token for one in-flight Managed authority attempt."""

    generation: GenerationT
    access: AccessT
    acquisition: ResourceAcquisition


@dataclass(frozen=True, slots=True)
class Idle(Generic[GenerationT]):
    generation: GenerationT


@dataclass(frozen=True, slots=True)
class Preparing(Generic[GenerationT, AccessT]):
    generation: GenerationT
    access: AccessT
    attempt: ManagedAttempt[GenerationT, AccessT]


@dataclass(frozen=True, slots=True)
class Current(Generic[GenerationT, AccessT, ResourceSetT, CapabilityT]):
    generation: GenerationT
    access: AccessT
    capability: CapabilityT
    resource_lease: ResourceLease[AccessT, ResourceSetT]


ManagedState: TypeAlias = (
    Idle[GenerationT]
    | Preparing[GenerationT, AccessT]
    | Current[GenerationT, AccessT, ResourceSetT, CapabilityT]
)


__all__ = ["Current", "Idle", "ManagedAttempt", "ManagedState", "Preparing"]
