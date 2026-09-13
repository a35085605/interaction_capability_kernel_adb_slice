from __future__ import annotations

from _lifecycle_new.capability.supervision.policy import (
    AcquireSupervisionPolicy,
    ReleaseSupervisionPolicy,
)


AdbTransportListWatchAcquireSupervisionPolicy = AcquireSupervisionPolicy
AdbTransportListWatchReleaseSupervisionPolicy = ReleaseSupervisionPolicy


__all__ = [
    "AdbTransportListWatchAcquireSupervisionPolicy",
    "AdbTransportListWatchReleaseSupervisionPolicy",
]
