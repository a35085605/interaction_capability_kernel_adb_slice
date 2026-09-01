from __future__ import annotations

from collections.abc import Callable

from adb.aosp.errors import AdbProtocolError
from adb.aosp.io.smart_socket import AdbServiceClient
from adb.aosp.protocol.smart_socket.services import (
    transport_features_by_id_service,
    transport_features_by_serial_service,
)
from networking import TcpAddress
from adb.transport.features import AdbTransportFeatures
from adb.transport.selection import (
    AdbTransportById,
    AdbTransportBySerial,
    AdbTransportSelector,
)


_ClientFactory = Callable[[TcpAddress], AdbServiceClient]


def _default_client_factory(endpoint: TcpAddress) -> AdbServiceClient:
    return AdbServiceClient(endpoint.host, endpoint.port)


def parse_transport_features(payload: bytes) -> AdbTransportFeatures:
    """Translate one native comma-separated feature payload into a domain value."""

    if not isinstance(payload, bytes):
        raise TypeError("ADB transport feature payload must be bytes")
    try:
        text = payload.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise AdbProtocolError("ADB feature list is not valid UTF-8") from exc
    return AdbTransportFeatures(frozenset(part for part in text.split(",") if part))


def _feature_service(selector: AdbTransportSelector) -> str:
    if isinstance(selector, AdbTransportBySerial):
        return transport_features_by_serial_service(selector.serial.value)
    if isinstance(selector, AdbTransportById):
        return transport_features_by_id_service(selector.transport_id.value)
    raise TypeError("selector must be AdbTransportBySerial or AdbTransportById")


class SmartSocketAdbTransportFeaturesReader:
    """Adapt a domain transport selector to one native feature query."""

    def __init__(self, *, _client_factory: _ClientFactory = _default_client_factory) -> None:
        self._client_factory = _client_factory

    def read(
        self,
        endpoint: TcpAddress,
        selector: AdbTransportSelector,
    ) -> AdbTransportFeatures:
        if not isinstance(endpoint, TcpAddress):
            raise TypeError("endpoint must be TcpAddress")
        payload = self._client_factory(endpoint).host_query(
            _feature_service(selector)
        )
        return parse_transport_features(payload)


__all__ = ["SmartSocketAdbTransportFeaturesReader", "parse_transport_features"]
