from __future__ import annotations

from typing import Protocol, TypeVar

from adb._resource.acquisition import ResourceAcquisition
from adb._resource.pool import ResourceEntry


RequirementsT = TypeVar("RequirementsT", contravariant=True)


class ResourceLifecycle(Protocol[RequirementsT]):
    """Physical acquire/cleanup operations for one adapter domain."""

    def acquire(
        self,
        requirements: RequirementsT,
        acquisition: ResourceAcquisition,
    ) -> None: ...

    def cleanup(self, entries: tuple[ResourceEntry, ...]) -> None: ...


__all__ = ["ResourceLifecycle"]
