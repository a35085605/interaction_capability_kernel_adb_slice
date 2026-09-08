from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from adb.transport.address import AdbConnectAddress
from native_attempt import NativeAttemptResult


@dataclass(frozen=True, slots=True)
class AdbTcpConnect:
    """Request an ADB connection attempt to an explicit TCP address."""

    address: AdbConnectAddress

    def __post_init__(self) -> None:
        if not isinstance(self.address, AdbConnectAddress):
            raise TypeError("address must be AdbConnectAddress")


@dataclass(frozen=True, slots=True)
class AdbTcpDisconnect:
    """Request an ADB disconnection attempt from an explicit TCP address."""

    address: AdbConnectAddress

    def __post_init__(self) -> None:
        if not isinstance(self.address, AdbConnectAddress):
            raise TypeError("address must be AdbConnectAddress")


class AdbTcpConnector(Protocol):
    """Execute explicit TCP transport connect attempts."""

    def connect(self, operation: AdbTcpConnect) -> NativeAttemptResult: ...


class AdbTcpDisconnector(Protocol):
    """Execute explicit TCP transport disconnect attempts."""

    def disconnect(self, operation: AdbTcpDisconnect) -> NativeAttemptResult: ...


__all__ = [
    "AdbTcpConnect",
    "AdbTcpConnector",
    "AdbTcpDisconnect",
    "AdbTcpDisconnector",
]
