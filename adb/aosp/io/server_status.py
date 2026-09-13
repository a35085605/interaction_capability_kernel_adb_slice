from __future__ import annotations

from collections.abc import Callable
from typing import Protocol

from adb.aosp.io.smart_socket import AdbServiceClient
from adb.aosp.model.server_status import AdbServerStatus, parse_server_status
from networking import TcpEndpoint


class AdbServerStatusReader(Protocol):
    """Read the current AOSP host-side ADB server status for one TCP server endpoint."""

    def read(self, server_endpoint: TcpEndpoint) -> AdbServerStatus:
        ...


_ClientFactory = Callable[[TcpEndpoint], AdbServiceClient]


def _default_client_factory(server_endpoint: TcpEndpoint) -> AdbServiceClient:
    return AdbServiceClient(server_endpoint.host, server_endpoint.port)


class SmartSocketAdbServerStatusReader:
    """Read one AOSP ``host:server-status`` query over smart socket."""

    def __init__(self, *, _client_factory: _ClientFactory = _default_client_factory) -> None:
        self._client_factory = _client_factory

    def read(self, server_endpoint: TcpEndpoint) -> AdbServerStatus:
        if not isinstance(server_endpoint, TcpEndpoint):
            raise TypeError("server_endpoint must be TcpEndpoint")
        payload = self._client_factory(server_endpoint).host_query(
            "host:server-status"
        )
        return parse_server_status(payload)


__all__ = ["AdbServerStatusReader", "SmartSocketAdbServerStatusReader"]
