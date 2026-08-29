from __future__ import annotations

from collections.abc import Callable

from adb.aosp.errors import AdbProtocolError
from adb.aosp.protocol.smart_socket.client import AdbServiceClient
from adb.aosp.protocol.smart_socket.services import (
    transport_features_by_id_service,
    transport_features_by_serial_service,
)
from adb.server.identity import AdbServer
from adb.transport.features import AdbTransportFeatures
from adb.transport.selection import (
    AdbTransportById,
    AdbTransportBySerial,
    AdbTransportSelector,
)


_ClientFactory = Callable[[AdbServer], AdbServiceClient]


def _default_client_factory(server: AdbServer) -> AdbServiceClient:
    return AdbServiceClient(server.endpoint)


def _feature_service(selector: AdbTransportSelector) -> str:
    if isinstance(selector, AdbTransportBySerial):
        return transport_features_by_serial_service(selector.serial.value)
    if isinstance(selector, AdbTransportById):
        return transport_features_by_id_service(selector.transport_id.value)
    raise TypeError("selector must be AdbTransportBySerial or AdbTransportById")


def _decode_features(payload: bytes) -> frozenset[str]:
    try:
        text = payload.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise AdbProtocolError("ADB feature list is not valid UTF-8") from exc
    return frozenset(part for part in text.split(",") if part)


class SmartSocketAdbTransportFeaturesReader:
    """One-shot feature reader for one selected transport."""

    def __init__(self, *, _client_factory: _ClientFactory = _default_client_factory) -> None:
        self._client_factory = _client_factory

    def read(self, server: AdbServer, selector: AdbTransportSelector) -> AdbTransportFeatures:
        if not isinstance(server, AdbServer):
            raise TypeError("server must be AdbServer")
        payload = self._client_factory(server).host_query(_feature_service(selector))
        return AdbTransportFeatures(_decode_features(payload))


__all__ = ["SmartSocketAdbTransportFeaturesReader"]
