from __future__ import annotations

from collections.abc import Callable
from typing import Protocol

from adb.aosp.io.smart_socket import AdbServiceClient
from adb.aosp.model.server_status import AdbServerStatus, parse_server_status
from networking import TcpAddress


class AdbServerStatusReader(Protocol):
    """Read the current AOSP host-side ADB server status for one TCP endpoint."""

    def read(self, address: TcpAddress) -> AdbServerStatus:
        ...


_ClientFactory = Callable[[TcpAddress], AdbServiceClient]


def _default_client_factory(endpoint: TcpAddress) -> AdbServiceClient:
    return AdbServiceClient(endpoint.host, endpoint.port)


class SmartSocketAdbServerStatusReader:
    """Read one AOSP ``host:server-status`` query over smart socket."""

    def __init__(self, *, _client_factory: _ClientFactory = _default_client_factory) -> None:
        self._client_factory = _client_factory

    def read(self, address: TcpAddress) -> AdbServerStatus:
        if not isinstance(address, TcpAddress):
            raise TypeError("address must be TcpAddress")
        payload = self._client_factory(address).host_query(
            "host:server-status"
        )
        return parse_server_status(payload)


__all__ = ["AdbServerStatusReader", "SmartSocketAdbServerStatusReader"]
