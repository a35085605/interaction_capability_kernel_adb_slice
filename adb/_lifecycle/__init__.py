"""Shared private lifecycle state-machine, result, state, and diagnostic primitives."""

from adb._lifecycle.resource import ResourceCleanupAttempt, ResourceOwnership, ResourceScope
from adb._lifecycle.snapshot import LifecycleSnapshot
from adb._lifecycle.state_machine import LifecycleStateMachine, PendingSnapshot
from adb._lifecycle.diagnostics import LifecycleDiagnostics
from adb._lifecycle.managed import AcquireAttemptGuard, ManagedLifecycle
from adb._lifecycle.result import (
    AcquireAttempt,
    AcquireBlocked,
    AcquireCommitted,
    AcquireExisting,
    AcquireFailed,
    AcquireStartBlocked,
    AcquireStartBusy,
    AcquireStartExisting,
    AcquireStartResult,
    AcquireSuperseded,
    CleanupRegistrationError,
    ReleaseAcquisitionRevoked,
    ReleaseGenerationMismatch,
    ReleaseInactive,
    ReleaseAccessDetached,
    ReleaseResult,
)


__all__ = [
    "AcquireAttempt",
    "AcquireAttemptGuard",
    "AcquireBlocked",
    "AcquireCommitted",
    "AcquireExisting",
    "AcquireFailed",
    "AcquireStartBlocked",
    "AcquireStartBusy",
    "AcquireStartExisting",
    "AcquireStartResult",
    "AcquireSuperseded",
    "CleanupRegistrationError",
    "LifecycleDiagnostics",
    "LifecycleSnapshot",
    "LifecycleStateMachine",
    "ManagedLifecycle",
    "PendingSnapshot",
    "ReleaseAcquisitionRevoked",
    "ReleaseGenerationMismatch",
    "ReleaseInactive",
    "ReleaseAccessDetached",
    "ReleaseResult",
    "ResourceCleanupAttempt",
    "ResourceOwnership",
    "ResourceScope",
]
