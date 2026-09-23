"""Generation-scoped acquire and release supervision for capability lifecycles."""

from lifecycle.capability.supervision.acquire import (
    AcquireDisposition,
    AcquireSupervisionResult,
    AcquireSupervisor,
    classify_acquire_result,
)
from lifecycle.capability.supervision.control import (
    CancellationSignal,
    SupervisionStopped,
    SupervisionStopReason,
)
from lifecycle.capability.supervision.policy import (
    AcquireSupervisionPolicy,
    ReleaseSupervisionPolicy,
)
from lifecycle.capability.supervision.release import (
    ReleaseDisposition,
    ReleaseSupervisionResult,
    ReleaseSupervisor,
    classify_release_result,
)

__all__ = [
    "AcquireDisposition",
    "AcquireSupervisionPolicy",
    "AcquireSupervisionResult",
    "AcquireSupervisor",
    "CancellationSignal",
    "ReleaseDisposition",
    "ReleaseSupervisionPolicy",
    "ReleaseSupervisionResult",
    "ReleaseSupervisor",
    "SupervisionStopped",
    "SupervisionStopReason",
    "classify_acquire_result",
    "classify_release_result",
]
