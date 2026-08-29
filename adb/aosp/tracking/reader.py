from __future__ import annotations

from collections.abc import Callable
from typing import Protocol, runtime_checkable

from adb.aosp.protocol.smart_socket.client import AdbServiceClient
from adb.aosp.protocol.smart_socket.services import TRACK_DEVICES_PROTO_BINARY_SERVICE
from adb.aosp.server.address import AdbServerTcpAddress
from adb.aosp.tracking.decoder import parse_devices
from adb.aosp.tracking.model import Devices


@runtime_checkable
class AdbDevicesReader(Protocol):
    """Read one complete native AOSP ``track-devices`` observation."""

    def read(self, address: AdbServerTcpAddress) -> Devices:
        ...


_ClientFactory = Callable[[AdbServerTcpAddress], AdbServiceClient]


def _default_client_factory(address: AdbServerTcpAddress) -> AdbServiceClient:
    return AdbServiceClient(address)


class SmartSocketAdbDevicesReader:
    """One-shot reader for the first AOSP protobuf ``track-devices`` record."""

    def __init__(self, *, _client_factory: _ClientFactory = _default_client_factory) -> None:
        self._client_factory = _client_factory

    def read(self, address: AdbServerTcpAddress) -> Devices:
        if not isinstance(address, AdbServerTcpAddress):
            raise TypeError("address must be AdbServerTcpAddress")
        payload = self._client_factory(address).first_stream_frame(
            TRACK_DEVICES_PROTO_BINARY_SERVICE
        )
        return parse_devices(payload)


__all__ = ["AdbDevicesReader", "SmartSocketAdbDevicesReader"]
