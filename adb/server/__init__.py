"""ADB server identity, lifecycle, failure, and status contracts."""

from adb.server.availability import AdbServerUnavailableError

from adb.server.lifecycle.control.controller import AdbServerController
from adb.server.lifecycle.control.errors import (
    AdbServerControlError,
    AdbServerNativeLifetimeBusyError,
    AdbServerNativeTerminationUnprovenError,
    AdbServerStartDeferredError,
    AdbServerStartError,
    AdbServerStopError,
    AdbServerStopInProgressError,
)
from adb.server.lifecycle.control.backend import AdbServerBackend
from adb.server.failure import (
    AdbServerConnectionFailure,
    AdbServerFailure,
    AdbServerLaunchFailure,
    AdbServerLifecycleFailure,
    AdbServerLivenessFailure,
    AdbServerNativeLifetimeBusyFailure,
    AdbServerNativeTerminationUnprovenFailure,
    AdbServerProcessExitedFailure,
    AdbServerProtocolFailure,
    AdbServerRecoveryDeferredFailure,
    AdbServerRequestFailure,
    AdbServerServiceFailure,
    AdbServerStartDeferredFailure,
    AdbServerStopInProgressFailure,
    AdbServerTimeoutFailure,
)
from adb.server.identity import AdbServer, ServerEpoch, ServerEpochSequence
from adb.server.state import AdbServerState, AdbServerStateView, AdbServerStateWriter
from adb.server.endpoint import AdbServerEndpoint
from adb.server.status import AdbMdnsBackend, AdbServerStatus, AdbServerStatusReader, AdbUsbBackend
from adb.server.lifecycle.control.subprocess import SubprocessAdbServerBackend

__all__ = [
    "AdbServerBackend",
    "AdbMdnsBackend",
    "AdbServerConnectionFailure",
    "AdbServerControlError",
    "AdbServerController",
    "AdbServerEndpoint",
    "ServerEpoch",
    "ServerEpochSequence",
    "AdbServerFailure",
    "AdbServer",
    "AdbServerLaunchFailure",
    "AdbServerLifecycleFailure",
    "AdbServerLivenessFailure",
    "AdbServerNativeLifetimeBusyError",
    "AdbServerNativeLifetimeBusyFailure",
    "AdbServerNativeTerminationUnprovenError",
    "AdbServerNativeTerminationUnprovenFailure",
    "AdbServerProcessExitedFailure",
    "AdbServerProtocolFailure",
    "AdbServerRecoveryDeferredFailure",
    "AdbServerRequestFailure",
    "AdbServerServiceFailure",
    "AdbServerStartDeferredError",
    "AdbServerStartDeferredFailure",
    "AdbServerStartError",
    "AdbServerState",
    "AdbServerStateView",
    "AdbServerStateWriter",
    "AdbServerStatus",
    "AdbServerStatusReader",
    "AdbServerStopError",
    "AdbServerStopInProgressError",
    "AdbServerStopInProgressFailure",
    "AdbServerTimeoutFailure",
    "AdbServerUnavailableError",
    "AdbUsbBackend",
    "SubprocessAdbServerBackend",
]
