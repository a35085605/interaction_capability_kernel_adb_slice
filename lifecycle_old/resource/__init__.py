"""Contracts and result models for synchronous physical-resource management."""

from lifecycle_old.resource.cleanup import cleanup_reverse
from lifecycle_old.resource.contract import ResourceProvider, ResourceRequirementsResolver
from lifecycle_old.resource.driver import (
    RequirementAcquireFailed,
    RequirementAcquireInterrupted,
    RequirementAcquireResult,
    RequirementAcquireSucceeded,
    PhysicalResources,
    ResourceDriver,
)
from lifecycle_old.resource.provider import ResolvedResourceProvider
from lifecycle_old.resource.result import (
    ResourceAcquireFailed,
    ResourceAcquireInterrupted,
    ResourceAcquireResult,
    ResourceAcquireSucceeded,
    ResourceCleanupResult,
    ResourceCleanupStatus,
)


__all__ = [
    "cleanup_reverse",
    "RequirementAcquireFailed",
    "RequirementAcquireInterrupted",
    "RequirementAcquireResult",
    "RequirementAcquireSucceeded",
    "PhysicalResources",
    "ResourceAcquireFailed",
    "ResourceAcquireInterrupted",
    "ResourceAcquireResult",
    "ResourceAcquireSucceeded",
    "ResourceCleanupResult",
    "ResourceCleanupStatus",
    "ResolvedResourceProvider",
    "ResourceDriver",
    "ResourceProvider",
    "ResourceRequirementsResolver",
]
