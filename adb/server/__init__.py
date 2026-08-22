"""ADB server identity, control, lifecycle relationships, coordination, and status contracts."""

from adb.server.control import AdbServerController, SubprocessAdbServerController
from adb.server.coordination import AdbServerMutationReservedError
from adb.server.failure import (
    AdbServerCloseUnprovenFailure,
    AdbServerConnectionFailure,
    AdbServerFailure,
    AdbServerLaunchFailure,
    AdbServerLifecycleFailure,
    AdbServerLivenessFailure,
    AdbServerOwnershipLossFailure,
    AdbServerProcessExitedFailure,
    AdbServerProtocolFailure,
    AdbServerRequestFailure,
    AdbServerServiceFailure,
    AdbServerTimeoutFailure,
)
from adb.server.identity import AdbServer
from adb.server.endpoint import AdbServerEndpoint
from adb.server.ownership import (
    ANY_ADB_SERVER_TERMINATION_POLICY,
    AdbServerOwnership,
    AdbServerOwnershipLostError,
    AdbServerStaleOwnerError,
    AdbServerTerminationPolicy,
    OWNED_ONLY_ADB_SERVER_TERMINATION_POLICY,
    acquire_process_adb_server,
    close_process_adb_server,
    invalidate_process_adb_server,
)
from adb.server.status import AdbMdnsBackend, AdbServerStatus, AdbServerStatusReader, AdbUsbBackend

__all__ = [
    "ANY_ADB_SERVER_TERMINATION_POLICY",
    "AdbServerOwnership",
    "AdbMdnsBackend",
    "AdbServerCloseUnprovenFailure",
    "AdbServerConnectionFailure",
    "AdbServerController",
    "AdbServerEndpoint",
    "AdbServerFailure",
    "AdbServer",
    "AdbServerLaunchFailure",
    "AdbServerLifecycleFailure",
    "AdbServerLivenessFailure",
    "AdbServerMutationReservedError",
    "AdbServerOwnershipLossFailure",
    "AdbServerOwnershipLostError",
    "AdbServerProcessExitedFailure",
    "AdbServerProtocolFailure",
    "AdbServerRequestFailure",
    "AdbServerServiceFailure",
    "AdbServerStaleOwnerError",
    "AdbServerTerminationPolicy",
    "AdbServerStatus",
    "AdbServerStatusReader",
    "AdbServerTimeoutFailure",
    "AdbUsbBackend",
    "OWNED_ONLY_ADB_SERVER_TERMINATION_POLICY",
    "SubprocessAdbServerController",
    "acquire_process_adb_server",
    "close_process_adb_server",
    "invalidate_process_adb_server",
]
