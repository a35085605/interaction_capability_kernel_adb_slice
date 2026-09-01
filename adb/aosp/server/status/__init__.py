"""Compatibility facade for relocated AOSP server-status model and adapter APIs."""

from adb.adapters.aosp.server_status import AdbServerStatusReader
from adb.aosp.model.server_status import AdbMdnsBackend, AdbServerStatus, AdbUsbBackend

__all__ = [
    "AdbMdnsBackend",
    "AdbServerStatus",
    "AdbServerStatusReader",
    "AdbUsbBackend",
]
