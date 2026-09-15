"""Generation-scoped acquire and release supervision for capability lifecycles."""

from lifecycle.capability.supervision.acquire import (
    AcquireSupervisionResult,
    AcquireSupervisor,
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
    ReleaseSupervisionResult,
    ReleaseSupervisor,
)

__all__ = [
    "AcquireSupervisionPolicy",
    "AcquireSupervisionResult",
    "AcquireSupervisor",
    "CancellationSignal",
    "ReleaseSupervisionPolicy",
    "ReleaseSupervisionResult",
    "ReleaseSupervisor",
    "SupervisionStopped",
    "SupervisionStopReason",
]
