"""ADB transport inventory facts, reads, projections, and single-use tracking."""

from adb.transport.inventory.lookup import AdbTrackedDeviceLookup
from adb.transport.inventory.model import (
    AdbConnectionState,
    AdbConnectionType,
    AdbDevicesSnapshot,
    AdbTrackedDevice,
)
from adb.transport.inventory.reader import AdbDevicesSnapshotReader
from adb.transport.inventory.resolution import (
    AdbConfiguredTransportResolution,
    AdbConfiguredTransportResolutionStatus,
    resolve_configured_transport,
)
from adb.transport.inventory.start import (
    AdbDevicesTrackingReadiness,
    AdbDevicesTrackingStart,
    AdbDevicesTrackingStartOrchestrator,
    AdbDevicesTrackingStartPolicy,
    AdbDevicesTrackingStartResult,
    AdbDevicesTrackingStartStatus,
)
from adb.transport.inventory.tracker import (
    AdbDevicesTracker,
    AdbDevicesTrackingScope,
)

__all__ = [
    "AdbConfiguredTransportResolution",
    "AdbConfiguredTransportResolutionStatus",
    "AdbConnectionState",
    "AdbConnectionType",
    "AdbDevicesSnapshot",
    "AdbDevicesSnapshotReader",
    "AdbDevicesTracker",
    "AdbDevicesTrackingScope",
    "AdbDevicesTrackingReadiness",
    "AdbDevicesTrackingStart",
    "AdbDevicesTrackingStartOrchestrator",
    "AdbDevicesTrackingStartPolicy",
    "AdbDevicesTrackingStartResult",
    "AdbDevicesTrackingStartStatus",
    "AdbTrackedDevice",
    "AdbTrackedDeviceLookup",
    "resolve_configured_transport",
]
