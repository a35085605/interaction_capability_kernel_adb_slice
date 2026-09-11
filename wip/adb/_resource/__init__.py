"""Access-keyed physical ResourceSet acquisition, retention, and cleanup primitives."""

from adb._resource.acquisition import ResourceAcquisition
from adb._resource.lifecycle import ResourceLifecycle
from adb._resource.pool import (
    GLOBAL_RESOURCE_POOL,
    ResourcePool,
    ResourceRecord,
    ResourceReservation,
)


__all__ = [
    "GLOBAL_RESOURCE_POOL",
    "ResourceAcquisition",
    "ResourceLifecycle",
    "ResourcePool",
    "ResourceRecord",
    "ResourceReservation",
]
