"""ADB server identity, lifecycle, failure, and status contracts."""

from adb.server.availability import AdbServerUnavailableError

from adb.server.lifecycle.control.port import (
    AdbServerControlError,
    AdbServerController,
    AdbServerStartError,
    AdbServerStopError,
)
from adb.server.failure import (
    AdbServerCloseUnprovenFailure,
    AdbServerConnectionFailure,
    AdbServerFailure,
    AdbServerLaunchFailure,
    AdbServerLifecycleFailure,
    AdbServerLivenessFailure,
    AdbServerProcessExitedFailure,
    AdbServerProtocolFailure,
    AdbServerRequestFailure,
    AdbServerServiceFailure,
    AdbServerTimeoutFailure,
)
from adb.server.identity import AdbServer, AdbServerEpochSequence
from adb.server.endpoint import AdbServerEndpoint
from adb.server.status import AdbMdnsBackend, AdbServerStatus, AdbServerStatusReader, AdbUsbBackend
from adb.server.lifecycle.control.adapter.subprocess import SubprocessAdbServerController

__all__ = [
    "AdbMdnsBackend",
    "AdbServerCloseUnprovenFailure",
    "AdbServerConnectionFailure",
    "AdbServerControlError",
    "AdbServerController",
    "AdbServerEndpoint",
    "AdbServerEpochSequence",
    "AdbServerFailure",
    "AdbServer",
    "AdbServerLaunchFailure",
    "AdbServerLifecycleFailure",
    "AdbServerLivenessFailure",
    "AdbServerProcessExitedFailure",
    "AdbServerProtocolFailure",
    "AdbServerRequestFailure",
    "AdbServerServiceFailure",
    "AdbServerStartError",
    "AdbServerStatus",
    "AdbServerStatusReader",
    "AdbServerStopError",
    "AdbServerTimeoutFailure",
    "AdbServerUnavailableError",
    "AdbUsbBackend",
    "SubprocessAdbServerController",
]
