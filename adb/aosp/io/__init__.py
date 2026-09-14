"""AOSP ADB I/O primitives."""

from adb.aosp.io.cli import AospAdbCliClient
from adb.aosp.io.server_process import (
    AospAdbServerProcessDriver,
    AospAdbServerStartError,
    AospAdbServerTerminationUnconfirmed,
    AospOwnedAdbServerProcess,
)
from adb.aosp.io.server_status import (
    AdbServerStatusReader,
    SmartSocketAdbServerStatusReader,
)
from adb.aosp.io.smart_socket import AdbServiceClient, ShellV2Result
from adb.aosp.io.track_devices import (
    AospTrackDevicesSession,
    AospTrackDevicesSessionDriver,
    SmartSocketAospTrackDevicesReader,
)
from adb.aosp.io.transport_features import SmartSocketAospTransportFeaturesReader


__all__ = [
    "AdbServerStatusReader",
    "AdbServiceClient",
    "AospAdbCliClient",
    "AospAdbServerProcessDriver",
    "AospAdbServerStartError",
    "AospAdbServerTerminationUnconfirmed",
    "AospOwnedAdbServerProcess",
    "AospTrackDevicesSession",
    "AospTrackDevicesSessionDriver",
    "ShellV2Result",
    "SmartSocketAdbServerStatusReader",
    "SmartSocketAospTrackDevicesReader",
    "SmartSocketAospTransportFeaturesReader",
]
