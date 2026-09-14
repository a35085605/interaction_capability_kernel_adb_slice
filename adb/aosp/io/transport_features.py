from __future__ import annotations

from collections.abc import Callable

from adb.aosp.io.smart_socket import AdbServiceClient
from adb.aosp.model.transport_features import parse_transport_features
from adb.aosp.protocol.smart_socket.services import (
    transport_features_by_id_service,
    transport_features_by_serial_service,
)
from networking import TcpEndpoint


_ClientFactory = Callable[[TcpEndpoint], AdbServiceClient]


def _default_client_factory(server_endpoint: TcpEndpoint) -> AdbServiceClient:
    return AdbServiceClient(server_endpoint.host, server_endpoint.port)


class SmartSocketAospTransportFeaturesReader:
    """Read raw AOSP transport features through smart-socket host queries."""

    def __init__(self, *, _client_factory: _ClientFactory = _default_client_factory) -> None:
        if not callable(_client_factory):
            raise TypeError("_client_factory must be callable")
        self._client_factory = _client_factory

    def read_by_serial(
        self,
        server_endpoint: TcpEndpoint,
        serial: str,
    ) -> frozenset[str]:
        return self._read(
            server_endpoint,
            transport_features_by_serial_service(serial),
        )

    def read_by_id(
        self,
        server_endpoint: TcpEndpoint,
        transport_id: int,
    ) -> frozenset[str]:
        return self._read(
            server_endpoint,
            transport_features_by_id_service(transport_id),
        )

    def _read(self, server_endpoint: TcpEndpoint, service: str) -> frozenset[str]:
        if not isinstance(server_endpoint, TcpEndpoint):
            raise TypeError("server_endpoint must be TcpEndpoint")
        payload = self._client_factory(server_endpoint).host_query(service)
        return parse_transport_features(payload)


__all__ = ["SmartSocketAospTransportFeaturesReader"]
