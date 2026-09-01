"""Compatibility imports for relocated AOSP server-status adapters."""

from adb.adapters.aosp.server_status import (
    AdbServerStatusReader,
    SmartSocketAdbServerStatusReader,
)

__all__ = ["AdbServerStatusReader", "SmartSocketAdbServerStatusReader"]
