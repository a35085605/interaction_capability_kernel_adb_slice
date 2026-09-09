"""Shared private lifecycle state-machine, result, state, and diagnostic primitives."""

from adb._lifecycle.endpoint import EndpointAccess, EndpointState
from adb._lifecycle.resource import ResourceCleanupAttempt, ResourceOwnership, ResourceScope
from adb._lifecycle.state_machine import LifecycleSnapshot, LifecycleStateMachine, PendingSnapshot
from adb._lifecycle.diagnostics import LifecycleDiagnostics
from adb._lifecycle.managed import ManagedLifecycle
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
    "EndpointAccess",
    "EndpointState",
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
