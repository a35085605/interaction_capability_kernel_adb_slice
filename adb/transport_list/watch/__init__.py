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
    AdbTransportListWatchBackend,
    AdbTransportListWatchBackendAcquired,
    AdbTransportListWatchBackendAcquireDeferred,
    AdbTransportListWatchBackendAcquireFailed,
    AdbTransportListWatchBackendAcquireRevoked,
    AdbTransportListWatchBackendAcquireResult,
    AdbTransportListWatchBackendAlreadyAcquired,
    AdbTransportListWatchBackendFactory,
    AdbTransportListWatchBackendReleased,
    AdbTransportListWatchBackendReleaseInactive,
    AdbTransportListWatchBackendReleaseMismatch,
    AdbTransportListWatchBackendReleaseResult,
)
from adb.transport_list.watch.controller import (
    AdbTransportListWatchController,
    AdbTransportListWatchStartCancelled,
    AdbTransportListWatchStartFailed,
    AdbTransportListWatchStartResult,
    AdbTransportListWatchStartSucceeded,
    AdbTransportListWatchStartSuperseded,
    ThreadedAdbTransportListWatchController,
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
from adb.transport_list.watch.attachment import AdbTransportListWatchAttachment
from adb.transport_list.watch.signal import (
    AdbTransportListWatchFailed,
    AdbTransportListWatchSignal,
    AdbTransportListWatchStarted,
    AdbTransportListWatchStopped,
)

__all__ = [
    "AdbTransportListWatchAttachment",
    "AdbTransportListWatchBackend",
    "AdbTransportListWatchBackendAcquired",
    "AdbTransportListWatchBackendAcquireDeferred",
    "AdbTransportListWatchBackendAcquireFailed",
    "AdbTransportListWatchBackendAcquireRevoked",
    "AdbTransportListWatchBackendAcquireResult",
    "AdbTransportListWatchBackendAlreadyAcquired",
    "AdbTransportListWatchBackendReleased",
    "AdbTransportListWatchBackendReleaseInactive",
    "AdbTransportListWatchBackendReleaseMismatch",
    "AdbTransportListWatchBackendReleaseResult",
    "AdbTransportListWatchGeneration",
    "AdbTransportListWatchGenerationIssuer",
    "AdbTransportListWatchState",
    "AdbTransportListWatchStateView",
    "AdbTransportListWatchBackendFactory",
    "AdbTransportListWatchCancelledError",
    "AdbTransportListWatchController",
    "AdbTransportListWatchError",
    "AdbTransportListWatchFailed",
    "AdbTransportListWatchFailure",
    "AdbTransportListWatchProtocolFailure",
    "AdbTransportListWatchServerConnectionFailure",
    "AdbTransportListWatchServiceFailure",
    "AdbTransportListWatchStream",
    "AdbTransportListWatchSignal",
    "AdbTransportListWatchStartCancelled",
    "AdbTransportListWatchStartFailed",
    "AdbTransportListWatchStartResult",
    "AdbTransportListWatchStartSucceeded",
    "AdbTransportListWatchStartSuperseded",
    "AdbTransportListWatchStarted",
    "AdbTransportListWatchStopped",
    "ThreadedAdbTransportListWatchController",
]
