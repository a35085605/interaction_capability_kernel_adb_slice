from __future__ import annotations

from adb.aosp.tracking.model import ConnectionType, Device, Devices
from adb.tracking.observation import (
    AdbObservedTransportKind,
    AdbTrackedTransportObservation,
)
from adb.transport.configuration import AdbTransportType
from adb.transport.identity import AdbTransportId


def _translate_transport_kind(value: ConnectionType | int) -> AdbObservedTransportKind:
    if value is ConnectionType.UNKNOWN:
        return AdbObservedTransportKind.unspecified()
    if value is ConnectionType.USB:
        return AdbObservedTransportKind.recognized(AdbTransportType.USB)
    if value is ConnectionType.SOCKET:
        return AdbObservedTransportKind.recognized(AdbTransportType.TCP)
    return AdbObservedTransportKind.unrecognized(int(value))


def to_tracked_transport_observation(device: Device) -> AdbTrackedTransportObservation:
    """Translate one raw AOSP device row at the protocol/domain boundary."""

    if not isinstance(device, Device):
        raise TypeError("device must be AOSP Device")
    transport_id = AdbTransportId(device.transport_id) if device.transport_id > 0 else None
    return AdbTrackedTransportObservation(
        serial_text=device.serial,
        transport_kind=_translate_transport_kind(device.connection_type),
        transport_id=transport_id,
    )


def to_tracked_transport_observations(
    devices: Devices,
) -> tuple[AdbTrackedTransportObservation, ...]:
    """Translate one complete raw AOSP devices payload into domain observations."""

    if not isinstance(devices, Devices):
        raise TypeError("devices must be AOSP Devices")
    return tuple(to_tracked_transport_observation(device) for device in devices.devices)


__all__ = [
    "to_tracked_transport_observation",
    "to_tracked_transport_observations",
]
