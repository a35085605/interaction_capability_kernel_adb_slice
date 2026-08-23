"""Compatibility exports for subprocess-backed ADB transport lifecycle control."""

from adb.transport.lifecycle.control.subprocess import (
    SubprocessAdbTransport,
    SubprocessAdbTransportController,
)

__all__ = ["SubprocessAdbTransport", "SubprocessAdbTransportController"]
