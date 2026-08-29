from __future__ import annotations

from collections.abc import Callable
from typing import Protocol

from adb.aosp.protocol.smart_socket.client import AdbServiceClient
from adb.aosp.server.status.decoder import parse_server_status
from adb.aosp.server.address import AdbServerAddress
from adb.aosp.server.status.model import AdbServerStatus


class AdbServerStatusReader(Protocol):
    """Read the current AOSP host-side ADB server status."""

    def read(self, address: AdbServerAddress) -> AdbServerStatus:
        ...


_ClientFactory = Callable[[AdbServerAddress], AdbServiceClient]


def _default_client_factory(address: AdbServerAddress) -> AdbServiceClient:
    return AdbServiceClient(address)


class SmartSocketAdbServerStatusReader:
    """One-shot reader for AOSP ``host:server-status``."""

    def __init__(self, *, _client_factory: _ClientFactory = _default_client_factory) -> None:
        self._client_factory = _client_factory

    def read(self, address: AdbServerAddress) -> AdbServerStatus:
        if not isinstance(address, AdbServerAddress):
            raise TypeError("address must be AdbServerAddress")
        payload = self._client_factory(address).host_query("host:server-status")
        return parse_server_status(payload)


__all__ = ["AdbServerStatusReader", "SmartSocketAdbServerStatusReader"]
