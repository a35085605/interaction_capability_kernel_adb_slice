from __future__ import annotations

from typing import Protocol

from adb.adapters.aosp.tracking import AdbDevicesReader, SmartSocketAdbDevicesReader
from adb.epoch import EpochIssuer
from adb.server.address import AdbServerTcpAddress
from adb.tracking.snapshot.identity import (
    AdbDevicesSnapshot,
    AdbDevicesSnapshotEpoch,
)


class AdbDevicesSnapshotReader(Protocol):
    """Read one freshly identified complete ADB track-devices snapshot."""

    def read(self, endpoint: AdbServerTcpAddress) -> AdbDevicesSnapshot:
        ...


class SmartSocketAdbDevicesSnapshotReader:
    """Identify translated smart-socket tracking observations as a domain snapshot."""

    def __init__(
        self,
        *,
        devices_snapshot_epoch_issuer: EpochIssuer[AdbDevicesSnapshotEpoch],
        _devices_reader: AdbDevicesReader | None = None,
    ) -> None:
        if not isinstance(devices_snapshot_epoch_issuer, EpochIssuer):
            raise TypeError("devices_snapshot_epoch_issuer must satisfy EpochIssuer")
        if _devices_reader is None:
            _devices_reader = SmartSocketAdbDevicesReader()
        if not isinstance(_devices_reader, AdbDevicesReader):
            raise TypeError("_devices_reader must satisfy AdbDevicesReader")
        self._devices_snapshot_epoch_issuer = devices_snapshot_epoch_issuer
        self._devices_reader = _devices_reader

    def read(self, endpoint: AdbServerTcpAddress) -> AdbDevicesSnapshot:
        if not isinstance(endpoint, AdbServerTcpAddress):
            raise TypeError("endpoint must be AdbServerTcpAddress")
        return AdbDevicesSnapshot(
            observations=self._devices_reader.read(endpoint),
            epoch=self._devices_snapshot_epoch_issuer.issue(),
        )


__all__ = ["AdbDevicesSnapshotReader", "SmartSocketAdbDevicesSnapshotReader"]
