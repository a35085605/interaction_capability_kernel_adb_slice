"""Shared private lifecycle state-machine, result, snapshot, and diagnostic primitives."""

from adb._lifecycle.resource import ResourceCleanupAttempt, ResourceOwnership, ResourceScope
from adb._lifecycle.snapshot import Snapshot
from adb._lifecycle.state_machine import LifecycleStateMachine, PendingSnapshot
from adb._lifecycle.diagnostics import LifecycleDiagnostics
from adb._lifecycle.managed import AcquireAttemptGuard, ManagedLifecycle
from adb._lifecycle.result import (
    AcquireAbandonResult,
    AcquireAttempt,
    AcquireAttemptAbandoned,
    AcquireAttemptCommitted,
    AcquireAttemptRevoked,
    AcquireAccessMismatch,
    AcquireBlocked,
    AcquireCommitResult,
    AcquireCommitted,
    AcquireExisting,
    AcquireFailed,
    AcquireStartBlocked,
    AcquireStartBusy,
    AcquireStartCurrent,
    AcquireStartResult,
    AcquireSuperseded,
    CleanupRegistrationError,
    GenerationMismatch,
    ReleaseAccessDetached,
    ReleaseAccessMismatch,
    ReleaseAcquisitionRevoked,
    ReleaseInactive,
    ReleaseResult,
)


__all__ = [
    "AcquireAbandonResult",
    "AcquireAttempt",
    "AcquireAttemptAbandoned",
    "AcquireAttemptCommitted",
    "AcquireAttemptGuard",
    "AcquireAttemptRevoked",
    "AcquireAccessMismatch",
    "AcquireBlocked",
    "AcquireCommitResult",
    "AcquireCommitted",
    "AcquireExisting",
    "AcquireFailed",
    "AcquireStartBlocked",
    "AcquireStartBusy",
    "AcquireStartCurrent",
    "AcquireStartResult",
    "AcquireSuperseded",
    "CleanupRegistrationError",
    "GenerationMismatch",
    "LifecycleDiagnostics",
    "LifecycleStateMachine",
    "ManagedLifecycle",
    "PendingSnapshot",
    "ReleaseAccessDetached",
    "ReleaseAccessMismatch",
    "ReleaseAcquisitionRevoked",
    "ReleaseInactive",
    "ReleaseResult",
    "ResourceCleanupAttempt",
    "ResourceOwnership",
    "ResourceScope",
    "Snapshot",
]
