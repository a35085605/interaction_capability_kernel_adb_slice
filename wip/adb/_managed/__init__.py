"""Managed Access authority and coordination primitives."""

from adb._managed.adapter import AccessModel, Adapter
from adb._managed.coordinator import ManagedCoordinator
from adb._managed.result import (
    AcquireBusy,
    AcquireCommitted,
    AcquireSuperseded,
    GenerationMismatch,
    ReleaseDetached,
    ReleaseInactive,
)
from adb._managed.snapshot import Snapshot
from adb._managed.state import Current, Idle, ManagedAttempt, ManagedState, Preparing


__all__ = [
    "AccessModel",
    "AcquireBusy",
    "AcquireCommitted",
    "AcquireSuperseded",
    "Adapter",
    "Current",
    "GenerationMismatch",
    "Idle",
    "ManagedAttempt",
    "ManagedCoordinator",
    "ManagedState",
    "Preparing",
    "ReleaseDetached",
    "ReleaseInactive",
    "Snapshot",
]
