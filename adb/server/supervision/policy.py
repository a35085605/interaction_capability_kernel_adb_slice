from __future__ import annotations

from _lifecycle_new.capability.supervision.policy import (
    AcquireSupervisionPolicy,
    ReleaseSupervisionPolicy,
)


AdbServerAcquireSupervisionPolicy = AcquireSupervisionPolicy
AdbServerReleaseSupervisionPolicy = ReleaseSupervisionPolicy


__all__ = [
    "AdbServerAcquireSupervisionPolicy",
    "AdbServerReleaseSupervisionPolicy",
]
