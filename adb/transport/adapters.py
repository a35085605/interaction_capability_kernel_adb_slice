from __future__ import annotations

from collections.abc import Callable

from adb._internal.client import AdbServiceClient
from adb.server.endpoint import AdbServerEndpoint
from adb.transport.features import AdbTransportFeatures
from adb.transport.selection import AdbTransportSelector


_ClientFactory = Callable[[AdbServerEndpoint], AdbServiceClient]


def _default_client_factory(endpoint: AdbServerEndpoint) -> AdbServiceClient:
    return AdbServiceClient(endpoint)


class SmartSocketAdbTransportFeaturesReader:
    """One-shot feature reader for one selected transport."""

    def __init__(self, *, _client_factory: _ClientFactory = _default_client_factory) -> None:
        self._client_factory = _client_factory

    def read(self, endpoint: AdbServerEndpoint, selector: AdbTransportSelector) -> AdbTransportFeatures:
        if not isinstance(endpoint, AdbServerEndpoint):
            raise TypeError("endpoint must be AdbServerEndpoint")
        return AdbTransportFeatures(self._client_factory(endpoint).features(selector))


__all__ = ["SmartSocketAdbTransportFeaturesReader"]
