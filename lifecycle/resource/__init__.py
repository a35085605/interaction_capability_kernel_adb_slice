"""Contracts and result models for synchronous physical-resource management."""

from lifecycle.resource.cleanup import cleanup_reverse
from lifecycle.resource.contract import ResourceProvider, ResourceRequirementsResolver
from lifecycle.resource.driver import (
    RequirementAcquireFailed,
    RequirementAcquireInterrupted,
    RequirementAcquireResult,
    RequirementAcquireSucceeded,
    PhysicalResources,
    ResourceDriver,
)
from lifecycle.resource.provider import ResolvedResourceProvider
from lifecycle.resource.result import (
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
    "ResolvedResourceProvider",
    "ResourceDriver",
    "ResourceProvider",
    "ResourceRequirementsResolver",
]
