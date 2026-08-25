"""ADB server identity, lifecycle, failure, and status contracts."""

from adb.server.availability import AdbServerUnavailableError

from adb.server.lifecycle.control.controller import AdbServerController
from adb.server.lifecycle.control.errors import (
    AdbServerControlError,
    AdbServerStartError,
    AdbServerStopError,
)
from adb.server.lifecycle.control.port import (
    AdbEndpointController,
    AdbEndpointStarter,
    AdbEndpointStopper,
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
from adb.server.identity import AdbServer, ServerEpoch, ServerEpochSequence
from adb.server.state import AdbServerState, AdbServerStateView, AdbServerStateWriter
from adb.server.endpoint import AdbServerEndpoint
from adb.server.status import AdbMdnsBackend, AdbServerStatus, AdbServerStatusReader, AdbUsbBackend
from adb.server.lifecycle.control.subprocess import SubprocessAdbEndpointController

__all__ = [
    "AdbEndpointController",
    "AdbEndpointStarter",
    "AdbEndpointStopper",
    "AdbMdnsBackend",
    "AdbServerCloseUnprovenFailure",
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
    "AdbServerProcessExitedFailure",
    "AdbServerProtocolFailure",
    "AdbServerRequestFailure",
    "AdbServerServiceFailure",
    "AdbServerStartError",
    "AdbServerState",
    "AdbServerStateView",
    "AdbServerStateWriter",
    "AdbServerStatus",
    "AdbServerStatusReader",
    "AdbServerStopError",
    "AdbServerTimeoutFailure",
    "AdbServerUnavailableError",
    "AdbUsbBackend",
    "SubprocessAdbEndpointController",
]
