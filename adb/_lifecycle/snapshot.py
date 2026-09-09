from __future__ import annotations

from dataclasses import dataclass
from typing import Generic, TypeVar


GenerationT = TypeVar("GenerationT")
AccessT = TypeVar("AccessT")


@dataclass(frozen=True, slots=True)
class LifecycleSnapshot(Generic[GenerationT, AccessT]):
    """One atomic pairing of lifecycle authority generation and access information."""

    generation: GenerationT
    access: AccessT

    def __post_init__(self) -> None:
        if self.generation is None:
            raise TypeError("generation cannot be None")


__all__ = ["LifecycleSnapshot"]
