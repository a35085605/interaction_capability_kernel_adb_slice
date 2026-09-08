from __future__ import annotations

from typing import Protocol

from adb.transport.address import AdbConnectAddress
from native_attempt import NativeAttemptResult


class AdbTcpTransportController(Protocol):
    """Execute explicit ADB TCP transport connect and disconnect attempts."""

    def connect(self, address: AdbConnectAddress) -> NativeAttemptResult: ...

    def disconnect(self, address: AdbConnectAddress) -> NativeAttemptResult: ...


__all__ = ["AdbTcpTransportController"]
