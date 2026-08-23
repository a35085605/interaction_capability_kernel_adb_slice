"""Compatibility exports for transport-inventory tracker lifetimes."""

from adb.transport.inventory.tracking.tracker import (
    AdbDevicesTracker,
    AdbDevicesTrackingScope,
)

__all__ = ["AdbDevicesTracker", "AdbDevicesTrackingScope"]
