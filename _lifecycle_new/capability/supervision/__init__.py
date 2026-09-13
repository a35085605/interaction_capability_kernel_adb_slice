"""Generation-scoped acquire and release supervision for capability lifecycles."""

from _lifecycle_new.capability.supervision.acquire import (
    AcquireSupervisionResult,
    AcquireSupervisor,
)
from _lifecycle_new.capability.supervision.policy import (
    AcquireSupervisionPolicy,
    ReleaseSupervisionPolicy,
)
from _lifecycle_new.capability.supervision.release import (
    ReleaseSupervisionResult,
    ReleaseSupervisor,
)

__all__ = [
    "AcquireSupervisionPolicy",
    "AcquireSupervisionResult",
    "AcquireSupervisor",
    "ReleaseSupervisionPolicy",
    "ReleaseSupervisionResult",
    "ReleaseSupervisor",
]
