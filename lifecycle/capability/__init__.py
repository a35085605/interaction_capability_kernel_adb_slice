"""Contracts and result models for capability lifecycles."""

from lifecycle.capability.lifecycle import (
    CapabilityLifecycle,
    LifecycleSnapshotReader,
)
from lifecycle.capability.projection import CapabilityProjector
from lifecycle.capability.result import (
    AcquireAlreadyActive,
    AcquireFailed,
    AcquireReleaseRequired,
    AcquireRequestMismatch,
    AcquireResult,
    AcquireSucceeded,
    LifecycleBusy,
    GenerationMismatch,
    ReleaseAlreadyIdle,
    ReleaseFailed,
    ReleaseRequestMismatch,
    ReleaseResult,
    ReleaseSucceeded,
)
from lifecycle.capability.snapshot import LifecyclePhase, LifecycleSnapshot
from lifecycle.capability.supervision import (
    AcquireSupervisionPolicy,
    AcquireSupervisionResult,
    AcquireSupervisor,
    ReleaseSupervisionPolicy,
    ReleaseSupervisionResult,
    ReleaseSupervisor,
)


__all__ = [
    "AcquireSupervisionPolicy",
    "AcquireSupervisionResult",
    "AcquireSupervisor",
    "CapabilityLifecycle",
    "LifecycleSnapshotReader",
    "CapabilityProjector",
    "AcquireAlreadyActive",
    "AcquireFailed",
    "AcquireReleaseRequired",
    "AcquireRequestMismatch",
    "AcquireResult",
    "AcquireSucceeded",
    "LifecycleBusy",
    "GenerationMismatch",
    "LifecyclePhase",
    "LifecycleSnapshot",
    "ReleaseAlreadyIdle",
    "ReleaseFailed",
    "ReleaseRequestMismatch",
    "ReleaseResult",
    "ReleaseSucceeded",
    "ReleaseSupervisionPolicy",
    "ReleaseSupervisionResult",
    "ReleaseSupervisor",
]
