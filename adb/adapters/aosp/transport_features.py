from __future__ import annotations

from collections.abc import Callable

from adb.aosp.io.smart_socket import AdbServiceClient
from adb.aosp.io.transport_features import SmartSocketAospTransportFeaturesReader
from adb.aosp.model.transport_features import (
    parse_transport_features as parse_aosp_transport_features,
)
from adb.transport.features import AdbTransportFeatures
from adb.transport.selection import (
    AdbTransportById,
    AdbTransportBySerial,
    AdbTransportSelector,
)
from networking import TcpEndpoint


_ClientFactory = Callable[[TcpEndpoint], AdbServiceClient]


def _default_client_factory(server_endpoint: TcpEndpoint) -> AdbServiceClient:
    return AdbServiceClient(server_endpoint.host, server_endpoint.port)


def parse_transport_features(payload: bytes) -> AdbTransportFeatures:
    """Compatibility adapter from an AOSP feature payload to the domain model."""

    return AdbTransportFeatures(parse_aosp_transport_features(payload))


class SmartSocketAdbTransportFeaturesReader:
    """Read AOSP transport features and project them into the domain model."""

    def __init__(self, *, _client_factory: _ClientFactory = _default_client_factory) -> None:
        self._reader = SmartSocketAospTransportFeaturesReader(
            _client_factory=_client_factory
        )

    def read(
        self,
        server_endpoint: TcpEndpoint,
        selector: AdbTransportSelector,
    ) -> AdbTransportFeatures:
        if isinstance(selector, AdbTransportBySerial):
            features = self._reader.read_by_serial(
                server_endpoint,
                selector.serial.value,
            )
        elif isinstance(selector, AdbTransportById):
            features = self._reader.read_by_id(
                server_endpoint,
                selector.transport_id.value,
            )
        else:
            raise TypeError("selector must be AdbTransportBySerial or AdbTransportById")
        return AdbTransportFeatures(features)


__all__ = ["SmartSocketAdbTransportFeaturesReader", "parse_transport_features"]
