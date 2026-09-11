"""Physical ResourceSet acquisition, coexistence, retention, and cleanup primitives."""

from adb._resource.acquisition import ResourceAcquisition
from adb._resource.lifecycle import ResourceLifecycle
from adb._resource.pool import (
    GLOBAL_RESOURCE_POOL,
    ResourceLease,
    ResourcePool,
    ResourceRecord,
    ResourceReservation,
    RetiredResource,
)
from adb._resource.requirement import ResourcePolicy, ResourceRequirement


__all__ = [
    "GLOBAL_RESOURCE_POOL",
    "ResourceAcquisition",
    "ResourceLease",
    "ResourceLifecycle",
    "ResourcePolicy",
    "ResourcePool",
    "ResourceRecord",
    "ResourceRequirement",
    "ResourceReservation",
    "RetiredResource",
]
