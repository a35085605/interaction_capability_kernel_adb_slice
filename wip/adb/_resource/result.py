from __future__ import annotations

from dataclasses import dataclass
from typing import Generic, TypeVar

from adb._resource.pool import ResourceEntry


RequirementsT = TypeVar("RequirementsT")
ResolvedResourcesT = TypeVar("ResolvedResourcesT")


@dataclass(frozen=True, slots=True)
class Resolved(Generic[ResolvedResourcesT]):
    """Adapter-selected typed resource view plus the pool entries backing it."""

    resources: ResolvedResourcesT
    entries: tuple[ResourceEntry, ...]

    def __post_init__(self) -> None:
        if self.resources is None:
            raise TypeError("resources cannot be None")
        if not self.entries:
            raise ValueError("resolved resources must be backed by at least one entry")


@dataclass(frozen=True, slots=True)
class MissingResources(Generic[RequirementsT]):
    """Resolution could not satisfy the adapter's domain-specific requirements."""

    requirements: RequirementsT

    def __post_init__(self) -> None:
        if self.requirements is None:
            raise TypeError("requirements cannot be None")


__all__ = ["MissingResources", "Resolved"]
