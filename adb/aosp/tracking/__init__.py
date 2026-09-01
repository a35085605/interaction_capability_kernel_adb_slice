"""Compatibility facade for relocated AOSP tracking model and adapter APIs."""

from adb.adapters.aosp.tracking import (
    AdbDevicesReader,
    SmartSocketAdbDevicesReader,
    to_tracked_transport_observation,
    to_tracked_transport_observations,
)
from adb.aosp.model.tracking import ConnectionState, ConnectionType, Device, Devices

__all__ = [
    "AdbDevicesReader",
    "ConnectionState",
    "ConnectionType",
    "Device",
    "Devices",
    "SmartSocketAdbDevicesReader",
    "to_tracked_transport_observation",
    "to_tracked_transport_observations",
]
