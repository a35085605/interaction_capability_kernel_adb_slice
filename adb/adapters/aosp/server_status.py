from __future__ import annotations

from collections.abc import Callable
from typing import Protocol

from adb.aosp.io.smart_socket import AdbServiceClient
from adb.aosp.model.server_status import AdbServerStatus, parse_server_status
from adb.server.address import AdbServerTcpAddress


class AdbServerStatusReader(Protocol):
    """Read the current AOSP host-side ADB server status for one domain endpoint."""

    def read(self, address: AdbServerTcpAddress) -> AdbServerStatus:
        ...


_ClientFactory = Callable[[AdbServerTcpAddress], AdbServiceClient]


def _default_client_factory(endpoint: AdbServerTcpAddress) -> AdbServiceClient:
    return AdbServiceClient(endpoint.host, endpoint.port)


class SmartSocketAdbServerStatusReader:
    """Adapt a domain server endpoint to one AOSP ``host:server-status`` query."""

    def __init__(self, *, _client_factory: _ClientFactory = _default_client_factory) -> None:
        self._client_factory = _client_factory

    def read(self, address: AdbServerTcpAddress) -> AdbServerStatus:
        if not isinstance(address, AdbServerTcpAddress):
            raise TypeError("address must be AdbServerTcpAddress")
        payload = self._client_factory(address).host_query(
            "host:server-status"
        )
        return parse_server_status(payload)


__all__ = ["AdbServerStatusReader", "SmartSocketAdbServerStatusReader"]
