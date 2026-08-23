"""ADB transport inventory reads, projections, and tracking."""

from adb.transport.inventory.tracking.identity import (
    AdbDevicesTrackingGenerationIssuer,
    AdbDevicesTrackingGenerationSequence,
    AdbDevicesTrackingScopeIdentity,
)
from adb.transport.inventory.lookup import AdbTrackedDeviceLookup
from adb.transport.inventory.model import (
    AdbConnectionState,
    AdbConnectionType,
    AdbDevicesSnapshot,
    AdbTrackedDevice,
)
from adb.transport.inventory.reader import AdbDevicesSnapshotReader
from adb.transport.inventory.state import (
    AdbDevicesInventoryState,
    AdbDevicesInventoryView,
    AdbDevicesInventoryWriter,
)
from adb.transport.inventory.resolution import (
    AdbConfiguredTransportResolution,
    AdbConfiguredTransportResolutionStatus,
    resolve_configured_transport,
)
from adb.transport.inventory.tracking.tracker import (
    AdbDevicesTracker,
    AdbDevicesTrackingScope,
)

__all__ = [
    "AdbConfiguredTransportResolution",
    "AdbConfiguredTransportResolutionStatus",
    "AdbConnectionState",
    "AdbConnectionType",
    "AdbDevicesInventoryState",
    "AdbDevicesInventoryView",
    "AdbDevicesInventoryWriter",
    "AdbDevicesSnapshot",
    "AdbDevicesSnapshotReader",
    "AdbDevicesTracker",
    "AdbDevicesTrackingGenerationIssuer",
    "AdbDevicesTrackingGenerationSequence",
    "AdbDevicesTrackingScope",
    "AdbDevicesTrackingScopeIdentity",
    "AdbTrackedDevice",
    "AdbTrackedDeviceLookup",
    "resolve_configured_transport",
]
