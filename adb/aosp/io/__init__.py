"""AOSP ADB native I/O primitives independent of domain models."""

from adb.aosp.io.smart_socket import AdbServiceClient, ShellV2Result

__all__ = ["AdbServiceClient", "ShellV2Result"]
