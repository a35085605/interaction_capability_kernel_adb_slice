"""Compatibility exports for ADB errors formerly owned by the AOSP layer."""

from __future__ import annotations

from adb.errors import (
    AdbError,
    AdbProtocolError,
    AdbServerConnectionError,
    AdbServiceError,
    AdbTimeoutError,
)


__all__ = [
    "AdbError",
    "AdbProtocolError",
    "AdbServerConnectionError",
    "AdbServiceError",
    "AdbTimeoutError",
]
