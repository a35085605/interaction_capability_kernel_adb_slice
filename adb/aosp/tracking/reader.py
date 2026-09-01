"""Compatibility imports for relocated AOSP tracking adapters."""

from adb.adapters.aosp.tracking import AdbDevicesReader, SmartSocketAdbDevicesReader

__all__ = ["AdbDevicesReader", "SmartSocketAdbDevicesReader"]
