"""Host-side ADB native nouns and atomic read capabilities.

ADB server endpoint control, server identity, lifecycle relationships, and process
coordination are separate concepts. Pairing commands live under ``adb.pairing``; transport
inventory and tracking live under ``adb.transport``; Android framework queries reached through
ADB live under ``android.adb``.
"""

from adb.errors import (
    AdbError,
    AdbProtocolError,
    AdbRemoteCommandError,
    AdbServerConnectionError,
    AdbServiceError,
    AdbTimeoutError,
    AdbTransportAmbiguousError,
    AdbTransportNotFoundError,
    AdbTransportSelectionError,
    AdbTransportUnavailableError,
)
from adb.managed import AdbManagedRuntime, RegisteredTransport
from adb.server import (
    AdbServerController,
    AdbServer,
    AdbServerMutationReservedError,
    AdbServerOwnershipLostError,
    AdbServerStaleOwnerError,
    AdbServerStatusReader,
    SubprocessAdbServerController,
    acquire_process_adb_server,
    close_process_adb_server,
    invalidate_process_adb_server,
)
from adb.transport import (
    AdbConnectionState,
    AdbConnectionType,
    AdbDeviceSerial,
    AdbDevicesSnapshot,
    AdbDevicesSnapshotReader,
    AdbTrackedDevice,
    AdbTrackedDeviceLookup,
    AdbTransportById,
    AdbTransportBySerial,
    AdbTransportFeatures,
    AdbTransportFeaturesReader,
    AdbTransportId,
    AdbTransportSelector,
)

__all__ = [
    "AdbConnectionState",
    "AdbConnectionType",
    "AdbDeviceSerial",
    "AdbDevicesSnapshot",
    "AdbDevicesSnapshotReader",
    "AdbError",
    "AdbManagedRuntime",
    "AdbProtocolError",
    "AdbRemoteCommandError",
    "AdbServerConnectionError",
    "AdbServerController",
    "AdbServerMutationReservedError",
    "AdbServer",
    "AdbServerOwnershipLostError",
    "AdbServerStaleOwnerError",
    "AdbServerStatusReader",
    "AdbServiceError",
    "AdbTimeoutError",
    "AdbTrackedDevice",
    "AdbTrackedDeviceLookup",
    "AdbTransportAmbiguousError",
    "AdbTransportById",
    "AdbTransportBySerial",
    "AdbTransportFeatures",
    "AdbTransportFeaturesReader",
    "AdbTransportId",
    "AdbTransportNotFoundError",
    "AdbTransportSelectionError",
    "AdbTransportSelector",
    "AdbTransportUnavailableError",
    "RegisteredTransport",
    "SubprocessAdbServerController",
    "acquire_process_adb_server",
    "close_process_adb_server",
    "invalidate_process_adb_server",
]
