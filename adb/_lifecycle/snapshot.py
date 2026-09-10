from __future__ import annotations

from dataclasses import dataclass
from typing import Generic, TypeVar


GenerationT = TypeVar("GenerationT")
AccessT = TypeVar("AccessT")
CapabilityT = TypeVar("CapabilityT")


@dataclass(frozen=True, slots=True)
class Snapshot(Generic[GenerationT, AccessT, CapabilityT]):
    """One atomic lifecycle generation/access/capability snapshot.

    Committed state is represented as ``(generation, access, capability)``. Uncommitted state is
    represented as ``(generation, None, None)``. Capturing a snapshot does not lease or extend the
    lifetime of its capability; a concurrent release may revoke the generation immediately after
    the snapshot is returned.
    """

    generation: GenerationT
    access: AccessT | None = None
    capability: CapabilityT | None = None

    def __post_init__(self) -> None:
        if self.generation is None:
            raise TypeError("generation cannot be None")
        if (self.access is None) != (self.capability is None):
            raise ValueError("access and capability must either both be present or both be None")


__all__ = ["Snapshot"]
