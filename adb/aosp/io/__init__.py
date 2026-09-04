"""AOSP ADB native I/O primitives independent of domain models."""

from adb.aosp.io.smart_socket import AdbServiceClient, ShellV2Result
from adb.aosp.io.server_status import (
    AdbServerStatusReader,
    SmartSocketAdbServerStatusReader,
)

__all__ = [
    "AdbServerStatusReader",
    "AdbServiceClient",
    "ShellV2Result",
    "SmartSocketAdbServerStatusReader",
]
