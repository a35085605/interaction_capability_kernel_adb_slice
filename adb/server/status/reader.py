from __future__ import annotations

from collections.abc import Callable
from typing import Protocol

from adb._internal.client import AdbServiceClient
from adb._internal.proto import parse_server_status
from adb.server.endpoint import AdbServerEndpoint
from adb.server.status.model import AdbServerStatus


class AdbServerStatusReader(Protocol):
    """Read the current AOSP host-side ADB server status."""

    def read(self, endpoint: AdbServerEndpoint) -> AdbServerStatus:
        ...


_ClientFactory = Callable[[AdbServerEndpoint], AdbServiceClient]


def _default_client_factory(endpoint: AdbServerEndpoint) -> AdbServiceClient:
    return AdbServiceClient(endpoint)


class SmartSocketAdbServerStatusReader:
    """One-shot reader for AOSP ``host:server-status``."""

    def __init__(self, *, _client_factory: _ClientFactory = _default_client_factory) -> None:
        self._client_factory = _client_factory

    def read(self, endpoint: AdbServerEndpoint) -> AdbServerStatus:
        if not isinstance(endpoint, AdbServerEndpoint):
            raise TypeError("endpoint must be AdbServerEndpoint")
        payload = self._client_factory(endpoint).host_query("host:server-status")
        return parse_server_status(payload)


__all__ = ["AdbServerStatusReader", "SmartSocketAdbServerStatusReader"]
