"""ADB server identity, lifecycle, failure, and status contracts."""

from adb.server.availability import AdbServerUnavailableError

from adb.server.lifecycle.control.controller import AdbServerController
from adb.server.lifecycle.control.result import (
    AdbServerProvisionDeferred,
    AdbServerProvisionFailed,
    AdbServerProvisionResult,
    AdbServerProvisioned,
)
from adb.server.lifecycle.control.errors import (
    AdbServerAttachmentMismatchError,
    AdbServerControlError,
    AdbServerBackendBusyError,
    AdbServerStopError,
)
from adb.server.lifecycle.control.backend import AdbServerBackend
from adb.server.failure import (
    AdbServerConnectionFailure,
    AdbServerFailure,
    AdbServerLaunchFailure,
    AdbServerLifecycleFailure,
    AdbServerLivenessFailure,
    AdbServerBackendBusyFailure,
    AdbServerProcessExitedFailure,
    AdbServerProtocolFailure,
    AdbServerRecoveryDeferredFailure,
    AdbServerRequestFailure,
    AdbServerServiceFailure,
    AdbServerStartDeferredFailure,
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
    "AdbServerAttachmentMismatchError",
    "AdbServerControlError",
    "AdbServerController",
    "AdbServerProvisionDeferred",
    "AdbServerProvisionFailed",
    "AdbServerProvisionResult",
    "AdbServerProvisioned",
    "AdbServerEndpoint",
    "ServerEpoch",
    "ServerEpochSequence",
    "AdbServerFailure",
    "AdbServer",
    "AdbServerLaunchFailure",
    "AdbServerLifecycleFailure",
    "AdbServerLivenessFailure",
    "AdbServerBackendBusyError",
    "AdbServerBackendBusyFailure",
    "AdbServerProcessExitedFailure",
    "AdbServerProtocolFailure",
    "AdbServerRecoveryDeferredFailure",
    "AdbServerRequestFailure",
    "AdbServerServiceFailure",
    "AdbServerStartDeferredFailure",
    "AdbServerState",
    "AdbServerStateView",
    "AdbServerStateWriter",
    "AdbServerStatus",
    "AdbServerStatusReader",
    "AdbServerStopError",
    "AdbServerTimeoutFailure",
    "AdbServerUnavailableError",
    "AdbUsbBackend",
    "SubprocessAdbServerBackend",
]
