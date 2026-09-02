"""ADB server lifecycle control, runtime transactions, and supervision boundaries."""

from adb.server.lifecycle.transaction import (
    AdbServerBackendAcquireUnavailable,
    AdbServerBackendAcquireUsable,
    AdbServerProvisionAcquireStopped,
    AdbServerProvisionActivationAttempted,
    AdbServerProvisionStateConflict,
    AdbServerProvisionTransactionResult,
)

__all__ = [
    "AdbServerBackendAcquireUnavailable",
    "AdbServerBackendAcquireUsable",
    "AdbServerProvisionAcquireStopped",
    "AdbServerProvisionActivationAttempted",
    "AdbServerProvisionStateConflict",
    "AdbServerProvisionTransactionResult",
]
