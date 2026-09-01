from __future__ import annotations

from collections.abc import Callable

from adb.aosp.protocol.smart_socket.client import AdbServiceClient
from adb.aosp.protocol.smart_socket.services import (
    transport_features_by_id_service,
    transport_features_by_serial_service,
)
from adb.aosp.transport.features import parse_transport_features
from adb.server.address import AdbServerTcpAddress
from adb.transport.features import AdbTransportFeatures
from adb.transport.selection import (
    AdbTransportById,
    AdbTransportBySerial,
    AdbTransportSelector,
)


_ClientFactory = Callable[[AdbServerTcpAddress], AdbServiceClient]


def _default_client_factory(endpoint: AdbServerTcpAddress) -> AdbServiceClient:
    return AdbServiceClient(endpoint.host, endpoint.port)


def _feature_service(selector: AdbTransportSelector) -> str:
    if isinstance(selector, AdbTransportBySerial):
        return transport_features_by_serial_service(selector.serial.value)
    if isinstance(selector, AdbTransportById):
        return transport_features_by_id_service(selector.transport_id.value)
    raise TypeError("selector must be AdbTransportBySerial or AdbTransportById")


class SmartSocketAdbTransportFeaturesReader:
    """One-shot feature reader for one selected transport."""

    def __init__(self, *, _client_factory: _ClientFactory = _default_client_factory) -> None:
        self._client_factory = _client_factory

    def read(
        self,
        endpoint: AdbServerTcpAddress,
        selector: AdbTransportSelector,
    ) -> AdbTransportFeatures:
        if not isinstance(endpoint, AdbServerTcpAddress):
            raise TypeError("endpoint must be AdbServerTcpAddress")
        payload = self._client_factory(endpoint).host_query(_feature_service(selector))
        return parse_transport_features(payload)


__all__ = ["SmartSocketAdbTransportFeaturesReader"]
