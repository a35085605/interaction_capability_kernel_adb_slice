from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, TypeAlias

from adb.transport.configuration import AdbTcpAddress
from adb.transport.selection import (
    AdbTransportById,
    AdbTransportBySerial,
    AdbTransportSelector,
)
from native_attempt import NativeAttemptResult


def _require_selector(value: object) -> AdbTransportSelector:
    if not isinstance(value, (AdbTransportBySerial, AdbTransportById)):
        raise TypeError("selector must be an ADB transport selector")
    return value


@dataclass(frozen=True, slots=True)
class AdbTcpConnect:
    """Request one native attempt to connect one explicit TCP ADB endpoint."""

    address: AdbTcpAddress

    def __post_init__(self) -> None:
        if not isinstance(self.address, AdbTcpAddress):
            raise TypeError("address must be AdbTcpAddress")


@dataclass(frozen=True, slots=True)
class AdbTcpDisconnect:
    """Request one native attempt to disconnect one explicit TCP ADB endpoint."""

    address: AdbTcpAddress

    def __post_init__(self) -> None:
        if not isinstance(self.address, AdbTcpAddress):
            raise TypeError("address must be AdbTcpAddress")


@dataclass(frozen=True, slots=True)
class AdbTransportReconnect:
    """Request one host-side reconnect attempt for one selected transport."""

    selector: AdbTransportSelector

    def __post_init__(self) -> None:
        _require_selector(self.selector)


@dataclass(frozen=True, slots=True)
class AdbDeviceSideReconnect:
    """Request one selected device-side adbd reconnect attempt."""

    selector: AdbTransportSelector

    def __post_init__(self) -> None:
        _require_selector(self.selector)


@dataclass(frozen=True, slots=True)
class AdbOfflineTransportsReconnect:
    """Request one ADB reconnect-offline native attempt."""


class AdbTcpConnector(Protocol):
    def connect(self, operation: AdbTcpConnect) -> NativeAttemptResult: ...


class AdbTcpDisconnector(Protocol):
    def disconnect(self, operation: AdbTcpDisconnect) -> NativeAttemptResult: ...


class AdbTransportReconnector(Protocol):
    def reconnect(self, operation: AdbTransportReconnect) -> NativeAttemptResult: ...


class AdbDeviceSideReconnector(Protocol):
    def reconnect_device(self, operation: AdbDeviceSideReconnect) -> NativeAttemptResult: ...


class AdbOfflineTransportsReconnector(Protocol):
    def reconnect_offline(self, operation: AdbOfflineTransportsReconnect) -> NativeAttemptResult: ...


AdbTransportCommandOperation: TypeAlias = (
    AdbTcpConnect
    | AdbTcpDisconnect
    | AdbTransportReconnect
    | AdbDeviceSideReconnect
    | AdbOfflineTransportsReconnect
)


__all__ = [
    "AdbDeviceSideReconnect",
    "AdbDeviceSideReconnector",
    "AdbOfflineTransportsReconnect",
    "AdbOfflineTransportsReconnector",
    "AdbTcpConnect",
    "AdbTcpConnector",
    "AdbTcpDisconnect",
    "AdbTcpDisconnector",
    "AdbTransportCommandOperation",
    "AdbTransportReconnect",
    "AdbTransportReconnector",
]
