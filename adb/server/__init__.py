"""Process-owned ADB server lifecycle, endpoint, and status contracts."""

from adb.server.endpoint import AdbServerEndpoint
from adb.server.model import (
    AdbServerAvailability,
    AdbServerFailure,
    AdbServerFailureKind,
    AdbServerObservation,
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
    "AdbServerAvailability",
    "AdbServerEndpoint",
    "AdbServerFailure",
    "AdbServerFailureKind",
    "AdbServerObservation",
    "AdbServerOwnershipLostError",
    "AdbServerStaleOwnerError",
    "AdbServerStatus",
    "AdbServerStatusReader",
    "AdbUsbBackend",
    "acquire_process_adb_server",
    "close_process_adb_server",
    "invalidate_process_adb_server",
]
