"""ADB transport-list watch protocols, lifecycle control, results, and signals."""

from adb.transport_list.watch.generation import (
    AdbTransportListWatchGeneration,
    AdbTransportListWatchGenerationIssuer,
)
from adb.transport_list.watch.state import (
    AdbTransportListWatchState,
    AdbTransportListWatchStateView,
)
from adb.transport_list.watch.backend import (
    AdbTransportListWatchLifecycle,
    AdbTransportListWatchAcquisition,
    AdbTransportListWatchBackendAcquireBlocked,
    AdbTransportListWatchBackendAcquireCommitted,
    AdbTransportListWatchBackendAcquireFailed,
    AdbTransportListWatchBackendAcquireSuperseded,
    AdbTransportListWatchAcquireOutcome,
    AdbTransportListWatchBackendAcquireExisting,
    AdbTransportListWatchLifecycleFactory,
    AdbTransportListWatchBackendReleaseApplied,
    AdbTransportListWatchBackendReleaseInactive,
    AdbTransportListWatchBackendReleaseGenerationMismatch,
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
from adb.transport_list.watch.signal import (
    AdbTransportListWatchFailed,
    AdbTransportListWatchLifecycleSignal,
    AdbTransportListWatchReady,
    AdbTransportListWatchEnded,
)

__all__ = [
    "AdbTransportListWatchLifecycle",
    "AdbTransportListWatchAcquisition",
    "AdbTransportListWatchBackendAcquireBlocked",
    "AdbTransportListWatchBackendAcquireCommitted",
    "AdbTransportListWatchBackendAcquireFailed",
    "AdbTransportListWatchBackendAcquireSuperseded",
    "AdbTransportListWatchAcquireOutcome",
    "AdbTransportListWatchBackendAcquireExisting",
    "AdbTransportListWatchBackendReleaseApplied",
    "AdbTransportListWatchBackendReleaseInactive",
    "AdbTransportListWatchBackendReleaseGenerationMismatch",
    "AdbTransportListWatchReleaseOutcome",
    "AdbTransportListWatchGeneration",
    "AdbTransportListWatchGenerationIssuer",
    "AdbTransportListWatchState",
    "AdbTransportListWatchStateView",
    "AdbTransportListWatchLifecycleFactory",
    "AdbTransportListWatchCancelledError",
    "AdbTransportListWatchError",
    "AdbTransportListWatchFailed",
    "AdbTransportListWatchFailure",
    "AdbTransportListWatchProtocolFailure",
    "AdbTransportListWatchServerConnectionFailure",
    "AdbTransportListWatchServiceFailure",
    "AdbTransportListWatchStream",
    "AdbTransportListWatchLifecycleSignal",
    "AdbTransportListWatchReady",
    "AdbTransportListWatchEnded",
]
