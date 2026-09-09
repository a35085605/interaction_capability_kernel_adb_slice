"""Shared private lifecycle state-machine, result, and diagnostic primitives."""

from adb._lifecycle.resource import ResourceCleanupAttempt, ResourceOwnership, ResourceScope
from adb._lifecycle.state_machine import LifecycleSnapshot, LifecycleStateMachine, PendingSnapshot
from adb._lifecycle.diagnostics import LifecycleDiagnostics
from adb._lifecycle.result import (
    AcquireAttempt,
    AcquireBlocked,
    AcquireBusy,
    AcquireExisting,
    AcquireStartResult,
    CleanupRegistrationError,
    ReleaseAcquisitionRevoked,
    ReleaseGenerationMismatch,
    ReleaseInactive,
    ReleaseAccessDetached,
    ReleaseResult,
)


__all__ = [
    "AcquireAttempt",
    "AcquireBlocked",
    "AcquireBusy",
    "AcquireExisting",
    "AcquireStartResult",
    "LifecycleSnapshot",
    "CleanupRegistrationError",
    "LifecycleStateMachine",
    "LifecycleDiagnostics",
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
