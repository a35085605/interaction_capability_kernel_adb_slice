from __future__ import annotations

from dataclasses import dataclass

from adb.epoch import Epoch, EpochSequence
from adb.tracking.snapshot.model import AdbDevicesRecord


class AdbDevicesSnapshotEpoch(Epoch):
    """Runtime-scoped ordinal identity for observed device snapshots."""

    __slots__ = ()


class AdbDevicesSnapshotEpochSequence(EpochSequence[AdbDevicesSnapshotEpoch]):
    """Runtime-scoped monotonically increasing device-snapshot epoch issuer."""

    def __init__(self) -> None:
        super().__init__(AdbDevicesSnapshotEpoch)


@dataclass(frozen=True, slots=True)
class AdbDevicesSnapshot:
    """Identity for one complete devices observation within one runtime."""

    record: AdbDevicesRecord
    epoch: AdbDevicesSnapshotEpoch

    def __post_init__(self) -> None:
        if not isinstance(self.record, AdbDevicesRecord):
            raise TypeError("record must be AdbDevicesRecord")
        if not isinstance(self.epoch, AdbDevicesSnapshotEpoch):
            raise TypeError("epoch must be AdbDevicesSnapshotEpoch")


__all__ = [
    "AdbDevicesSnapshot",
    "AdbDevicesSnapshotEpoch",
    "AdbDevicesSnapshotEpochSequence",
]
