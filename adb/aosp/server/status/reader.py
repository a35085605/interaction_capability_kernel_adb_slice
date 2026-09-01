from __future__ import annotations

from collections.abc import Callable
from typing import Protocol

from adb.aosp.protocol.smart_socket.client import AdbServiceClient
from adb.aosp.server.status.decoder import parse_server_status
from adb.server.address import AdbServerTcpAddress
from adb.aosp.server.status.model import AdbServerStatus


class AdbServerStatusReader(Protocol):
    """Read the current AOSP host-side ADB server status."""

    def read(self, address: AdbServerTcpAddress) -> AdbServerStatus:
        ...


_ClientFactory = Callable[[AdbServerTcpAddress], AdbServiceClient]


def _default_client_factory(address: AdbServerTcpAddress) -> AdbServiceClient:
    return AdbServiceClient(address.host, address.port)


class SmartSocketAdbServerStatusReader:
    """One-shot reader for AOSP ``host:server-status``."""

    def __init__(self, *, _client_factory: _ClientFactory = _default_client_factory) -> None:
        self._client_factory = _client_factory

    def read(self, address: AdbServerTcpAddress) -> AdbServerStatus:
        if not isinstance(address, AdbServerTcpAddress):
            raise TypeError("address must be AdbServerTcpAddress")
        payload = self._client_factory(address).host_query("host:server-status")
        return parse_server_status(payload)


__all__ = ["AdbServerStatusReader", "SmartSocketAdbServerStatusReader"]
