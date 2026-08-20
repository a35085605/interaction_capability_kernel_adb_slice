"""Host-side ADB native nouns and atomic read capabilities.

Canonical ownership is noun-first around the process-owned ADB server, pairing
relationship, and transports. Pairing commands live under ``adb.pairing``;
transport inventory and tracking are owned by ``adb.transport``; Android
framework queries reached through ADB live under ``android.adb``.
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
    AdbServerAcquisitionPolicy,
    AdbServerConfigurationConflictError,
    AdbServerOwnershipLostError,
    AdbServerRef,
    AdbServerStatusReader,
    acquire_process_adb_server,
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
    "AdbServerAcquisitionPolicy",
    "AdbServerConfigurationConflictError",
    "AdbServerConnectionError",
    "AdbServerOwnershipLostError",
    "AdbServerRef",
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
    "acquire_process_adb_server",
]
