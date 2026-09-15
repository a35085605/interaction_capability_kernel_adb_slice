"""AOSP ADB protocol I/O primitives."""

from adb.aosp.io.cli import AospAdbCliClient
from adb.aosp.io.server_status import (
    AdbServerStatusReader,
    SmartSocketAdbServerStatusReader,
)
from adb.aosp.io.smart_socket import AdbServiceClient, ShellV2Result
from adb.aosp.io.track_devices import (
    AospTrackDevicesSession,
    AospTrackDevicesSessionOpenFailed,
    AospTrackDevicesSessionOpenResult,
    AospTrackDevicesSessionOpener,
    SmartSocketAospTrackDevicesReader,
)
from adb.aosp.io.transport_features import SmartSocketAospTransportFeaturesReader


__all__ = [
    "AdbServerStatusReader",
    "AdbServiceClient",
    "AospAdbCliClient",
    "AospTrackDevicesSession",
    "AospTrackDevicesSessionOpenFailed",
    "AospTrackDevicesSessionOpenResult",
    "AospTrackDevicesSessionOpener",
    "ShellV2Result",
    "SmartSocketAdbServerStatusReader",
    "SmartSocketAospTrackDevicesReader",
    "SmartSocketAospTransportFeaturesReader",
]
