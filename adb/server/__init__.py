"""ADB server endpoint, identity, lifecycle, failure, and status contracts."""

from adb.server.availability import AdbServerUnavailableError

from adb.server.lifecycle.control.errors import (
    AdbServerBootstrapError,
    AdbServerLifecycleConsistencyError,
    AdbServerLifecycleError,
)
from adb.server.lifecycle.control.backend import AdbServerBackend
from adb.server.failure import (
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
from adb.server.identity import AdbServerIdentity, AdbServerIdentityIssuer
from adb.server.state import (
    AdbServerActivated,
    AdbServerActivationStateConflict,
    AdbServerActivationResult,
    AdbServerDeactivated,
    AdbServerDeactivationStateConflict,
    AdbServerDeactivationResult,
    AdbServerState,
    AdbServerStateStatus,
    AdbServerStateStore,
    AdbServerStateView,
    AdbServerStateWriter,
)
from adb.server.endpoint import AdbServerEndpoint
from adb.aosp.model.server_status import AdbMdnsBackend, AdbServerStatus, AdbUsbBackend
from adb.adapters.aosp.server_status import AdbServerStatusReader

__all__ = [
    "AdbServerActivated",
    "AdbServerActivationStateConflict",
    "AdbServerActivationResult",
    "AdbServerBackend",
    "AdbServerDeactivated",
    "AdbServerDeactivationStateConflict",
    "AdbServerDeactivationResult",
    "AdbMdnsBackend",
    "AdbServerConnectionFailure",
    "AdbServerBootstrapError",
    "AdbServerLifecycleConsistencyError",
    "AdbServerLifecycleError",
    "AdbServerEndpoint",
    "AdbServerIdentity",
    "AdbServerIdentityIssuer",
    "AdbServerFailure",
    "AdbServerLaunchFailure",
    "AdbServerLifecycleFailure",
    "AdbServerLivenessFailure",
    "AdbServerProcessExitedFailure",
    "AdbServerProtocolFailure",
    "AdbServerRequestFailure",
    "AdbServerServiceFailure",
    "AdbServerState",
    "AdbServerStateStatus",
    "AdbServerStateStore",
    "AdbServerStateView",
    "AdbServerStateWriter",
    "AdbServerStatus",
    "AdbServerStatusReader",
    "AdbServerTimeoutFailure",
    "AdbServerUnavailableError",
    "AdbUsbBackend",
]
