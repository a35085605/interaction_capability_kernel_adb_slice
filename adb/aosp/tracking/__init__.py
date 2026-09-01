"""AOSP ``track-devices`` protocol models, decoding, and low-level streaming."""

from adb.aosp.tracking.model import ConnectionState, ConnectionType, Device, Devices
from adb.aosp.tracking.reader import AdbDevicesReader, SmartSocketAdbDevicesReader
from adb.aosp.tracking.observation import (
    to_tracked_transport_observation,
    to_tracked_transport_observations,
)

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
