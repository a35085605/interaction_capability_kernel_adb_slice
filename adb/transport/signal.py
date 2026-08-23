"""Compatibility aggregate for ADB transport lifecycle and inventory-tracking signals."""

from typing import TypeAlias

from adb.transport.inventory.tracking.signal import (
    AdbDevicesSnapshotObserved,
    AdbDevicesTrackingFailed,
    AdbDevicesTrackingFailure,
    AdbDevicesTrackingStarted,
    AdbDevicesTrackingStopped,
)
from adb.transport.lifecycle.control.port import AdbTransportCommandOperation
from adb.transport.lifecycle.signal import (
    AdbTransportCommandCompleted,
    AdbTransportEnsureCompleted,
)

AdbTransportSignal: TypeAlias = (
    AdbTransportCommandCompleted
    | AdbTransportEnsureCompleted
    | AdbDevicesTrackingStarted
    | AdbDevicesTrackingStopped
    | AdbDevicesTrackingFailed
    | AdbDevicesSnapshotObserved
)

__all__ = [
    "AdbDevicesSnapshotObserved",
    "AdbDevicesTrackingFailed",
    "AdbDevicesTrackingFailure",
    "AdbDevicesTrackingStarted",
    "AdbDevicesTrackingStopped",
    "AdbTransportCommandCompleted",
    "AdbTransportCommandOperation",
    "AdbTransportEnsureCompleted",
    "AdbTransportSignal",
]
