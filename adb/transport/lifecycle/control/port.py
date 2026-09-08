from __future__ import annotations

from typing import Protocol

from adb.transport.address import AdbConnectAddress
from adb.transport.lifecycle.control.result import (
    AdbTcpTransportConnectResult,
    AdbTcpTransportDisconnectResult,
)


class AdbTcpTransportController(Protocol):
    """Execute explicit ADB TCP transport connect and disconnect commands."""

    def connect(self, address: AdbConnectAddress) -> AdbTcpTransportConnectResult: ...

    def disconnect(self, address: AdbConnectAddress) -> AdbTcpTransportDisconnectResult: ...


__all__ = ["AdbTcpTransportController"]
