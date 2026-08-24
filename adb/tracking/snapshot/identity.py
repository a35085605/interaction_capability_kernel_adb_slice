from __future__ import annotations

from adb.epoch import Epoch, EpochSequence


class AdbDevicesSnapshotEpoch(Epoch):
    """Runtime-scoped ordinal identity for committed device snapshots."""

    __slots__ = ()


class AdbDevicesSnapshotEpochSequence(EpochSequence[AdbDevicesSnapshotEpoch]):
    """Runtime-scoped monotonically increasing device-snapshot epoch issuer."""

    def __init__(self) -> None:
        super().__init__(AdbDevicesSnapshotEpoch)


__all__ = [
    "AdbDevicesSnapshotEpoch",
    "AdbDevicesSnapshotEpochSequence",
]
