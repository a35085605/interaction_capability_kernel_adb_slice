from __future__ import annotations

from typing import Protocol, TypeVar

from adb._resource.acquisition import ResourceAcquisition


RequirementsT = TypeVar("RequirementsT", contravariant=True)
ResourceSetT = TypeVar("ResourceSetT")


class ResourceLifecycle(Protocol[RequirementsT, ResourceSetT]):
    """Physical acquire/cleanup operations for one adapter domain.

    ``acquire`` returns one complete ResourceSet. Any temporary/partial resources
    created before it returns remain the implementation's responsibility.
    """

    def acquire(
        self,
        requirements: RequirementsT,
        acquisition: ResourceAcquisition,
    ) -> ResourceSetT: ...

    def cleanup(self, resources: ResourceSetT) -> None: ...


__all__ = ["ResourceLifecycle"]
