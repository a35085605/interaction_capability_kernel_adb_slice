from __future__ import annotations

from collections.abc import Callable

from adb.aosp.io.smart_socket import AdbServiceClient
from adb.aosp.io.track_devices import SmartSocketAospTrackDevicesReader
from adb.aosp.model.track_devices import (
    ConnectionState,
    ConnectionType,
    Device,
    Devices,
)
from adb.transport.identity import AdbTransportId
from adb.transport.model import (
    AdbObservedTransportKind,
    AdbObservedTransportState,
    AdbTransport,
    AdbTransportKind,
    AdbTransportState,
)
from adb.transport_list.model import AdbTransportList
from networking import TcpEndpoint


_ClientFactory = Callable[[TcpEndpoint], AdbServiceClient]


def _default_client_factory(server_endpoint: TcpEndpoint) -> AdbServiceClient:
    return AdbServiceClient(server_endpoint.host, server_endpoint.port)


class SmartSocketAdbTransportListReader:
    """Read raw AOSP devices and project them into one domain transport list."""

    def __init__(self, *, _client_factory: _ClientFactory = _default_client_factory) -> None:
        self._reader = SmartSocketAospTrackDevicesReader(
            _client_factory=_client_factory
        )

    def read(
        self,
        server_endpoint: TcpEndpoint,
    ) -> AdbTransportList:
        return to_transport_list(self._reader.read(server_endpoint))


def _translate_transport_kind(value: ConnectionType | int) -> AdbObservedTransportKind:
    if value is ConnectionType.UNKNOWN:
        return AdbObservedTransportKind.unspecified()
    if value is ConnectionType.USB:
        return AdbObservedTransportKind.recognized(AdbTransportKind.USB)
    if value is ConnectionType.SOCKET:
        return AdbObservedTransportKind.recognized(AdbTransportKind.TCP)
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
