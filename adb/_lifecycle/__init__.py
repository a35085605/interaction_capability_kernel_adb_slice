"""Shared private lifecycle state-machine, result, and diagnostic primitives."""

from adb._lifecycle.state_machine import LifecycleSnapshot, LifecycleStateMachine, PendingSnapshot
from adb._lifecycle.diagnostics import LifecycleDiagnostics
from adb._lifecycle.result import (
    AcquireBlocked,
    AcquireBusy,
    AcquireExisting,
    AcquireStarted,
    AcquireStartResult,
    AcquireToken,
    CleanupRegistrationError,
    ReleaseAcquisitionRevoked,
    ReleaseGenerationMismatch,
    ReleaseInactive,
    ReleaseResourceDetached,
    ReleaseResult,
)


__all__ = [
    "AcquireBlocked",
    "AcquireBusy",
    "AcquireExisting",
    "AcquireStarted",
    "AcquireStartResult",
    "AcquireToken",
    "LifecycleSnapshot",
    "CleanupRegistrationError",
    "LifecycleStateMachine",
    "LifecycleDiagnostics",
    "PendingSnapshot",
    "ReleaseAcquisitionRevoked",
    "ReleaseGenerationMismatch",
    "ReleaseInactive",
    "ReleaseResourceDetached",
    "ReleaseResult",
]
