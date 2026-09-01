from __future__ import annotations

from typing import Protocol

from adb.adapters.aosp.track_devices import SmartSocketAdbTransportListReader
from adb.epoch import EpochIssuer
from networking import TcpAddress
from adb.tracking.watch import AdbTransportListReader
from adb.tracking.snapshot.identity import (
    AdbDevicesSnapshot,
    AdbDevicesSnapshotEpoch,
)


class AdbDevicesSnapshotReader(Protocol):
    """Read one freshly identified complete domain transport-list snapshot."""

    def read(self, endpoint: TcpAddress) -> AdbDevicesSnapshot:
        ...


class SmartSocketAdbDevicesSnapshotReader:
    """Identify translated smart-socket tracking observations as a domain snapshot."""

    def __init__(
        self,
        *,
        devices_snapshot_epoch_issuer: EpochIssuer[AdbDevicesSnapshotEpoch],
        _transport_list_reader: AdbTransportListReader | None = None,
    ) -> None:
        if not isinstance(devices_snapshot_epoch_issuer, EpochIssuer):
            raise TypeError("devices_snapshot_epoch_issuer must satisfy EpochIssuer")
        if _transport_list_reader is None:
            _transport_list_reader = SmartSocketAdbTransportListReader()
        if not isinstance(_transport_list_reader, AdbTransportListReader):
            raise TypeError("_transport_list_reader must satisfy AdbTransportListReader")
        self._devices_snapshot_epoch_issuer = devices_snapshot_epoch_issuer
        self._transport_list_reader = _transport_list_reader

    def read(self, endpoint: TcpAddress) -> AdbDevicesSnapshot:
        if not isinstance(endpoint, TcpAddress):
            raise TypeError("endpoint must be TcpAddress")
        return AdbDevicesSnapshot(
            observations=self._transport_list_reader.read(endpoint),
            epoch=self._devices_snapshot_epoch_issuer.issue(),
        )


__all__ = ["AdbDevicesSnapshotReader", "SmartSocketAdbDevicesSnapshotReader"]
