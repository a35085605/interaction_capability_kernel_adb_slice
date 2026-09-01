"""Compatibility imports for relocated AOSP tracking adapters."""

from adb.adapters.aosp.tracking import (
    AdbDevicesTrackingBackend,
    AdbDevicesTrackingBackendStream,
    SmartSocketAdbDevicesTrackingBackend,
    SmartSocketAdbDevicesTrackingStream,
)

__all__ = [
    "AdbDevicesTrackingBackend",
    "AdbDevicesTrackingBackendStream",
    "SmartSocketAdbDevicesTrackingBackend",
    "SmartSocketAdbDevicesTrackingStream",
]
