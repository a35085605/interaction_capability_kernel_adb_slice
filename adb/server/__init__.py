"""Process-owned ADB server lifecycle, endpoint, failure, and status contracts."""

from adb.server.model import (
    AdbServerEndpoint,
    AdbServerCloseUnprovenFailure,
    AdbServerConnectionFailure,
    AdbServerFailure,
    AdbServerLaunchFailure,
    AdbServerLifecycleFailure,
    AdbServerOwnershipLossFailure,
    AdbServerProcessExitedFailure,
    AdbServerProtocolFailure,
    AdbServerRequestFailure,
    AdbServerServiceFailure,
    AdbServerTimeoutFailure,
)
from adb.server.ownership import (
    AdbOwnedServer,
    AdbServerOwnershipLostError,
    AdbServerStaleOwnerError,
    acquire_process_adb_server,
    close_process_adb_server,
    invalidate_process_adb_server,
)
from adb.server.status import AdbMdnsBackend, AdbServerStatus, AdbServerStatusReader, AdbUsbBackend

__all__ = [
    "AdbMdnsBackend",
    "AdbOwnedServer",
    "AdbServerCloseUnprovenFailure",
    "AdbServerConnectionFailure",
    "AdbServerEndpoint",
    "AdbServerFailure",
    "AdbServerLaunchFailure",
    "AdbServerLifecycleFailure",
    "AdbServerOwnershipLossFailure",
    "AdbServerOwnershipLostError",
    "AdbServerProcessExitedFailure",
    "AdbServerProtocolFailure",
    "AdbServerRequestFailure",
    "AdbServerServiceFailure",
    "AdbServerStaleOwnerError",
    "AdbServerStatus",
    "AdbServerStatusReader",
    "AdbServerTimeoutFailure",
    "AdbUsbBackend",
    "acquire_process_adb_server",
    "close_process_adb_server",
    "invalidate_process_adb_server",
]
