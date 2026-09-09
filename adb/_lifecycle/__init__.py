"""Shared private lifecycle authority, result, and diagnostic primitives."""

from adb._lifecycle.authority import AuthoritySnapshot, LifecycleAuthority, PendingSnapshot
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
    ReleaseOwnershipDetached,
    ReleaseResult,
)


__all__ = [
    "AcquireBlocked",
    "AcquireBusy",
    "AcquireExisting",
    "AcquireStarted",
    "AcquireStartResult",
    "AcquireToken",
    "AuthoritySnapshot",
    "CleanupRegistrationError",
    "LifecycleAuthority",
    "LifecycleDiagnostics",
    "PendingSnapshot",
    "ReleaseAcquisitionRevoked",
    "ReleaseGenerationMismatch",
    "ReleaseInactive",
    "ReleaseOwnershipDetached",
    "ReleaseResult",
]
