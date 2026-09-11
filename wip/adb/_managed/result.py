from __future__ import annotations

from dataclasses import dataclass
from typing import Generic, TypeVar

from adb._managed.snapshot import Snapshot


GenerationT = TypeVar("GenerationT")
AccessT = TypeVar("AccessT")
CapabilityT = TypeVar("CapabilityT")


@dataclass(frozen=True, slots=True)
class GenerationMismatch(Generic[GenerationT]):
    current_generation: GenerationT


@dataclass(frozen=True, slots=True)
class AcquireBusy:
    """Another Managed preparation currently owns commit authority."""


@dataclass(frozen=True, slots=True)
class AcquireCommitted(Generic[GenerationT, AccessT, CapabilityT]):
    snapshot: Snapshot[GenerationT, AccessT, CapabilityT]


@dataclass(frozen=True, slots=True)
class AcquireSuperseded(Generic[GenerationT]):
    current_generation: GenerationT


@dataclass(frozen=True, slots=True)
class ReleaseInactive:
    """Current generation has no committed Access to detach."""


@dataclass(frozen=True, slots=True)
class ReleaseDetached(Generic[GenerationT]):
    next_generation: GenerationT


__all__ = [
    "AcquireBusy",
    "AcquireCommitted",
    "AcquireSuperseded",
    "GenerationMismatch",
    "ReleaseDetached",
    "ReleaseInactive",
]
