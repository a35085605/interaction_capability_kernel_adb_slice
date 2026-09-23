"""Contracts and result models for capability lifecycles."""

from lifecycle.capability.lifecycle import (
    CapabilityLifecycle,
    LifecycleSnapshotReader,
)
from lifecycle.capability.projection import CapabilityProjector
from lifecycle.capability.result import AcquireResult, LifecycleResult, ReleaseResult
from lifecycle.capability.snapshot import LifecyclePhase, LifecycleSnapshot
from lifecycle.capability.supervision import (
    AcquireDisposition,
    AcquireSupervisionPolicy,
    AcquireSupervisionResult,
    AcquireSupervisor,
    CancellationSignal,
    ReleaseDisposition,
    ReleaseSupervisionPolicy,
    ReleaseSupervisionResult,
    ReleaseSupervisor,
    SupervisionStopped,
    SupervisionStopReason,
    classify_acquire_result,
    classify_release_result,
)


__all__ = [
    "AcquireDisposition",
    "AcquireResult",
    "AcquireSupervisionPolicy",
    "AcquireSupervisionResult",
    "AcquireSupervisor",
    "CancellationSignal",
    "CapabilityLifecycle",
    "CapabilityProjector",
    "LifecyclePhase",
    "LifecycleResult",
    "LifecycleSnapshot",
    "LifecycleSnapshotReader",
    "ReleaseDisposition",
    "ReleaseResult",
    "ReleaseSupervisionPolicy",
    "ReleaseSupervisionResult",
    "ReleaseSupervisor",
    "SupervisionStopped",
    "SupervisionStopReason",
    "classify_acquire_result",
    "classify_release_result",
]
