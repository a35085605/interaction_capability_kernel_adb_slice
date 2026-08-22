from __future__ import annotations

from collections.abc import Callable

from adb._internal.client import AdbServiceClient
from adb.server.identity import AdbServer
from adb.transport.features import AdbTransportFeatures
from adb.transport.selection import AdbTransportSelector


_ClientFactory = Callable[[AdbServer], AdbServiceClient]


def _default_client_factory(server: AdbServer) -> AdbServiceClient:
    return AdbServiceClient(server.endpoint)


class SmartSocketAdbTransportFeaturesReader:
    """One-shot feature reader for one selected transport."""

    def __init__(self, *, _client_factory: _ClientFactory = _default_client_factory) -> None:
        self._client_factory = _client_factory

    def read(self, server: AdbServer, selector: AdbTransportSelector) -> AdbTransportFeatures:
        if not isinstance(server, AdbServer):
            raise TypeError("server must be AdbServer")
        return AdbTransportFeatures(self._client_factory(server).features(selector))


__all__ = ["SmartSocketAdbTransportFeaturesReader"]
