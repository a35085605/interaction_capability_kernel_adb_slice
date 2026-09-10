"""Shared private lifecycle state-machine, result, snapshot, and diagnostic primitives."""

from adb._lifecycle.resource import ResourceCleanupAttempt, ResourceOwnership, ResourceScope
from adb._lifecycle.snapshot import Snapshot
from adb._lifecycle.state_machine import LifecycleStateMachine, PendingSnapshot
from adb._lifecycle.diagnostics import LifecycleDiagnostics
from adb._lifecycle.managed import AcquireAttemptGuard, ManagedLifecycle
from adb._lifecycle.result import (
    AcquireAttempt,
    AcquireBlocked,
    AcquireCommitted,
    AcquireExisting,
    AcquireFailed,
    AcquireGenerationMismatch,
    AcquireStartBlocked,
    AcquireStartBusy,
    AcquireStartExisting,
    AcquireStartResult,
    AcquireSuperseded,
    CleanupRegistrationError,
    ReleaseAccessDetached,
    ReleaseAccessMismatch,
    ReleaseAcquisitionRevoked,
    ReleaseGenerationMismatch,
    ReleaseInactive,
    ReleaseResult,
)


__all__ = [
    "AcquireAttempt",
    "AcquireAttemptGuard",
    "AcquireBlocked",
    "AcquireCommitted",
    "AcquireExisting",
    "AcquireFailed",
    "AcquireGenerationMismatch",
    "AcquireStartBlocked",
    "AcquireStartBusy",
    "AcquireStartExisting",
    "AcquireStartResult",
    "AcquireSuperseded",
    "CleanupRegistrationError",
    "LifecycleDiagnostics",
    "LifecycleStateMachine",
    "ManagedLifecycle",
    "PendingSnapshot",
    "ReleaseAccessDetached",
    "ReleaseAccessMismatch",
    "ReleaseAcquisitionRevoked",
    "ReleaseGenerationMismatch",
    "ReleaseInactive",
    "ReleaseResult",
    "ResourceCleanupAttempt",
    "ResourceOwnership",
    "ResourceScope",
    "Snapshot",
]
