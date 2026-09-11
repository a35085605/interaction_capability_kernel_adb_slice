"""Managed Access authority, conflict policy, and coordination primitives."""

from adb._managed.adapter import AccessModel, Adapter
from adb._managed.coordinator import ManagedCoordinator
from adb._managed.pool import (
    GLOBAL_RESOURCE_POOL,
    ResourceLease,
    ResourcePool,
    ResourceRecord,
    ResourceReservation,
    RetiredResource,
)
from adb._managed.requirement import ResourcePolicy, ResourceRequirement
from adb._managed.result import (
    AcquireAccessMismatch,
    AcquireBusy,
    AcquireCommitted,
    AcquireExisting,
    AcquireResult,
    AcquireSuperseded,
    GenerationMismatch,
    ReleaseAccessMismatch,
    ReleaseAcquisitionRevoked,
    ReleaseDetached,
    ReleaseInactive,
    ReleaseResult,
)
from adb._managed.snapshot import Snapshot
from adb._managed.state import Current, Idle, ManagedAttempt, ManagedState, Preparing


__all__ = [
    "AccessModel",
    "AcquireAccessMismatch",
    "AcquireBusy",
    "AcquireCommitted",
    "AcquireExisting",
    "AcquireResult",
    "AcquireSuperseded",
    "Adapter",
    "Current",
    "GLOBAL_RESOURCE_POOL",
    "GenerationMismatch",
    "Idle",
    "ManagedAttempt",
    "ManagedCoordinator",
    "ManagedState",
    "Preparing",
    "ReleaseAccessMismatch",
    "ReleaseAcquisitionRevoked",
    "ReleaseDetached",
    "ReleaseInactive",
    "ReleaseResult",
    "ResourceLease",
    "ResourcePolicy",
    "ResourcePool",
    "ResourceRecord",
    "ResourceRequirement",
    "ResourceReservation",
    "RetiredResource",
    "Snapshot",
]
