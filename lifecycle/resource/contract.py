from __future__ import annotations

from typing import Protocol, TypeVar

from lifecycle.resource.driver import PhysicalResources
from lifecycle.resource.result import ResourceAcquireResult


RequestT = TypeVar("RequestT")
RequirementT = TypeVar("RequirementT")
PhysicalResourceT = TypeVar("PhysicalResourceT")


class ResourceRequirementsResolver(Protocol[RequestT, RequirementT]):
    """Resolve the ordered physical-resource requirements for one request."""

    def resolve(self, request: RequestT) -> tuple[RequirementT, ...]: ...


class ResourceProvider(Protocol[RequestT, PhysicalResourceT]):
    """Provide request-level physical-resource acquisition and release.

    ``acquire`` must report every resource whose ownership has been transferred and
    that still requires cleanup. Resources that were temporary and conclusively
    cleaned up before the result is returned are not transferred. A caller retains
    the reported resources and releases them later.

    ``release`` may be retried with the same resource tuple after raising. Implementations
    must therefore tolerate resources that were already cleaned up by an earlier attempt
    and only return after the supplied resources no longer require cleanup.
    """

    def acquire(self, request: RequestT) -> ResourceAcquireResult[PhysicalResourceT]: ...

    def release(self, resources: PhysicalResources[PhysicalResourceT]) -> None: ...


__all__ = ["ResourceProvider", "ResourceRequirementsResolver"]
