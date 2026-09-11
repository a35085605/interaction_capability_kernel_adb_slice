"""Physical resource acquisition, pooling, binding, retirement, and cleanup primitives."""

from adb._resource.acquisition import ResourceAcquisition
from adb._resource.lifecycle import ResourceLifecycle
from adb._resource.pool import (
    GLOBAL_RESOURCE_POOL,
    ResourceBinding,
    ResourceEntry,
    ResourcePool,
    ResourcePoolView,
)
from adb._resource.result import MissingResources, Resolved


__all__ = [
    "GLOBAL_RESOURCE_POOL",
    "MissingResources",
    "Resolved",
    "ResourceAcquisition",
    "ResourceBinding",
    "ResourceEntry",
    "ResourceLifecycle",
    "ResourcePool",
    "ResourcePoolView",
]
