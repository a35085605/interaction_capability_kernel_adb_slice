"""ADB transport-list watch protocols, lifecycle control, and results."""

from adb._lifecycle import (
    AcquireBlocked,
    AcquireCommitted,
    AcquireExisting,
    AcquireFailed,
    AcquireSuperseded,
    LifecycleSnapshot,
    ReleaseAccessDetached,
    ReleaseAcquisitionRevoked,
    ReleaseGenerationMismatch,
    ReleaseInactive,
)
from adb.transport_list.watch.generation import (
    AdbTransportListWatchGeneration,
    AdbTransportListWatchGenerationIssuer,
)
from adb.transport_list.watch.state import (
    AdbTransportListWatchState,
    AdbTransportListWatchStateView,
)
from adb.transport_list.watch.contract import (
    AdbTransportListWatchAccess,
    AdbTransportListWatchAcquireOutcome,
    AdbTransportListWatchLifecycle,
    AdbTransportListWatchLifecycleFactory,
    AdbTransportListWatchReleaseOutcome,
)
from adb.transport_list.watch.stream import AdbTransportListWatchStream
from adb.transport_list.watch.error import (
    AdbTransportListWatchCancelledError,
    AdbTransportListWatchError,
)
from adb.transport_list.watch.failure import (
    AdbTransportListWatchFailure,
    AdbTransportListWatchProtocolFailure,
    AdbTransportListWatchServerConnectionFailure,
    AdbTransportListWatchServiceFailure,
)

__all__ = [
    "AcquireBlocked",
    "AcquireCommitted",
    "AcquireExisting",
    "AcquireFailed",
    "AcquireSuperseded",
    "LifecycleSnapshot",
    "AdbTransportListWatchAccess",
    "AdbTransportListWatchAcquireOutcome",
    "AdbTransportListWatchCancelledError",
    "AdbTransportListWatchError",
    "AdbTransportListWatchFailure",
    "AdbTransportListWatchGeneration",
    "AdbTransportListWatchGenerationIssuer",
    "AdbTransportListWatchLifecycle",
    "AdbTransportListWatchLifecycleFactory",
    "AdbTransportListWatchProtocolFailure",
    "AdbTransportListWatchReleaseOutcome",
    "AdbTransportListWatchServerConnectionFailure",
    "AdbTransportListWatchServiceFailure",
    "AdbTransportListWatchState",
    "AdbTransportListWatchStateView",
    "AdbTransportListWatchStream",
    "ReleaseAccessDetached",
    "ReleaseAcquisitionRevoked",
    "ReleaseGenerationMismatch",
    "ReleaseInactive",
]
