from __future__ import annotations

from collections.abc import Callable

from adb.aosp.io.smart_socket import AdbServiceClient
from adb.aosp.model.track_devices import (
    ConnectionState,
    ConnectionType,
    Device,
    Devices,
    parse_devices,
)
from adb.aosp.protocol.smart_socket.services import TRACK_DEVICES_PROTO_BINARY_SERVICE
from adb.transport.configuration import AdbTransportType
from adb.transport.identity import AdbTransportId
from adb.transport.model import (
    AdbObservedTransportKind,
    AdbObservedTransportState,
    AdbTransport,
    AdbTransportState,
)
from adb.transport_list.model import AdbTransportList
from networking import TcpAddress


def _parse_transport_list(payload: bytes) -> AdbTransportList:
    return to_transport_list(parse_devices(payload))


_ClientFactory = Callable[[TcpAddress], AdbServiceClient]


def _default_client_factory(address: TcpAddress) -> AdbServiceClient:
    return AdbServiceClient(address.host, address.port)


class SmartSocketAdbTransportListReader:
    """Read and translate the first AOSP track-devices record for one endpoint."""

    def __init__(self, *, _client_factory: _ClientFactory = _default_client_factory) -> None:
        self._client_factory = _client_factory

    def read(
        self,
        address: TcpAddress,
    ) -> AdbTransportList:
        if not isinstance(address, TcpAddress):
            raise TypeError("address must be TcpAddress")
        payload = self._client_factory(address).first_stream_frame(
            TRACK_DEVICES_PROTO_BINARY_SERVICE
        )
        return _parse_transport_list(payload)


def _translate_transport_kind(value: ConnectionType | int) -> AdbObservedTransportKind:
    if value is ConnectionType.UNKNOWN:
        return AdbObservedTransportKind.unspecified()
    if value is ConnectionType.USB:
        return AdbObservedTransportKind.recognized(AdbTransportType.USB)
    if value is ConnectionType.SOCKET:
        return AdbObservedTransportKind.recognized(AdbTransportType.TCP)
    return AdbObservedTransportKind.unrecognized(int(value))


def _translate_transport_state(
    value: ConnectionState | int,
) -> AdbObservedTransportState:
    if value is ConnectionState.ANY:
        return AdbObservedTransportState.unspecified()

    translated = {
        ConnectionState.CONNECTING: AdbTransportState.CONNECTING,
        ConnectionState.AUTHORIZING: AdbTransportState.AUTHORIZING,
        ConnectionState.UNAUTHORIZED: AdbTransportState.UNAUTHORIZED,
        ConnectionState.NOPERMISSION: AdbTransportState.NO_PERMISSION,
        ConnectionState.DETACHED: AdbTransportState.DETACHED,
        ConnectionState.OFFLINE: AdbTransportState.OFFLINE,
        ConnectionState.BOOTLOADER: AdbTransportState.BOOTLOADER,
        ConnectionState.DEVICE: AdbTransportState.READY,
        ConnectionState.HOST: AdbTransportState.HOST,
        ConnectionState.RECOVERY: AdbTransportState.RECOVERY,
        ConnectionState.SIDELOAD: AdbTransportState.SIDELOAD,
        ConnectionState.RESCUE: AdbTransportState.RESCUE,
    }.get(value)
    if translated is None:
        return AdbObservedTransportState.unrecognized(int(value))
    return AdbObservedTransportState.recognized(translated)


def to_transport(device: Device) -> AdbTransport:
    """Translate an AOSP device row into an ``AdbTransport``."""

    if not isinstance(device, Device):
        raise TypeError("device must be AOSP Device")
    transport_id = AdbTransportId(device.transport_id) if device.transport_id > 0 else None
    return AdbTransport(
        serial_text=device.serial,
        transport_kind=_translate_transport_kind(device.connection_type),
        transport_id=transport_id,
        state=_translate_transport_state(device.state),
    )


def to_transport_list(devices: Devices) -> AdbTransportList:
    """Translate an AOSP devices payload into an ``AdbTransportList``."""

    if not isinstance(devices, Devices):
        raise TypeError("devices must be AOSP Devices")
    return AdbTransportList(to_transport(device) for device in devices.devices)


__all__ = [
    "SmartSocketAdbTransportListReader",
    "to_transport",
    "to_transport_list",
]
