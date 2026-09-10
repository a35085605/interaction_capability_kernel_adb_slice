from __future__ import annotations

from dataclasses import dataclass
from typing import Generic, TypeVar


GenerationT = TypeVar("GenerationT")
AccessT = TypeVar("AccessT")
CapabilityT = TypeVar("CapabilityT")


@dataclass(frozen=True, slots=True)
class LifecycleSnapshot(Generic[GenerationT, AccessT]):
    """One atomic pairing of lifecycle authority generation and access information."""

    generation: GenerationT
    access: AccessT

    def __post_init__(self) -> None:
        if self.generation is None:
            raise TypeError("generation cannot be None")


@dataclass(frozen=True, slots=True)
class CapabilitySnapshot(Generic[GenerationT, CapabilityT]):
    """One atomic pairing of lifecycle authority generation and operation capability.

    ``access`` is an alias of ``capability`` for snapshot consumers that use the shared
    access-oriented snapshot vocabulary. Neither name leases or extends the capability lifetime.
    """

    generation: GenerationT
    capability: CapabilityT

    def __post_init__(self) -> None:
        if self.generation is None:
            raise TypeError("generation cannot be None")

    @property
    def access(self) -> CapabilityT:
        """Return the captured capability through the shared snapshot access vocabulary."""

        return self.capability


__all__ = ["CapabilitySnapshot", "LifecycleSnapshot"]
