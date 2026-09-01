"""Compatibility aliases for the pre-backend tracking implementation names."""

from adb.adapters.aosp.tracking import (
    SmartSocketAdbDevicesTrackingBackend,
    SmartSocketAdbDevicesTrackingStream,
)

AdbDeviceTracker = SmartSocketAdbDevicesTrackingBackend
AdbDeviceTrackerStream = SmartSocketAdbDevicesTrackingStream

__all__ = ["AdbDeviceTracker", "AdbDeviceTrackerStream"]
