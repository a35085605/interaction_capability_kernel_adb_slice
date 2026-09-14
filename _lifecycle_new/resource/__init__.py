"""Contracts and result models for synchronous physical-resource management."""

from _lifecycle_new.resource.cleanup import cleanup_reverse
from _lifecycle_new.resource.contract import ResourceProvider, ResourceRequirementsResolver
from _lifecycle_new.resource.driver import (
    RequirementAcquireFailed,
    RequirementAcquireInterrupted,
    RequirementAcquireResult,
    RequirementAcquireSucceeded,
    PhysicalResources,
    ResourceDriver,
)
from _lifecycle_new.resource.result import (
    ResourceAcquireFailed,
    ResourceAcquireResult,
    ResourceAcquireSucceeded,
)


__all__ = [
    "cleanup_reverse",
    "RequirementAcquireFailed",
    "RequirementAcquireInterrupted",
    "RequirementAcquireResult",
    "RequirementAcquireSucceeded",
    "PhysicalResources",
    "ResourceAcquireFailed",
    "ResourceAcquireResult",
    "ResourceAcquireSucceeded",
    "ResourceDriver",
    "ResourceProvider",
    "ResourceRequirementsResolver",
]
