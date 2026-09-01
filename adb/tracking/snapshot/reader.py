from __future__ import annotations

from typing import Protocol

from adb.adapters.aosp.track_devices import SmartSocketAdbTransportListReader
from adb.epoch import EpochIssuer
from networking import TcpAddress
from adb.tracking.transport_list import AdbTransportListReader
from adb.tracking.snapshot.identity import (
    AdbTransportListSnapshot,
    AdbTransportListSnapshotEpoch,
)


class AdbTransportListSnapshotReader(Protocol):
    """Read one freshly identified complete domain transport-list snapshot."""

    def read(self, endpoint: TcpAddress) -> AdbTransportListSnapshot:
        ...


class SmartSocketAdbTransportListSnapshotReader:
    """Identify a translated smart-socket transport list as a domain snapshot."""

    def __init__(
        self,
        *,
        transport_list_snapshot_epoch_issuer: EpochIssuer[AdbTransportListSnapshotEpoch],
        _transport_list_reader: AdbTransportListReader | None = None,
    ) -> None:
        if not isinstance(transport_list_snapshot_epoch_issuer, EpochIssuer):
            raise TypeError("transport_list_snapshot_epoch_issuer must satisfy EpochIssuer")
        if _transport_list_reader is None:
            _transport_list_reader = SmartSocketAdbTransportListReader()
        if not isinstance(_transport_list_reader, AdbTransportListReader):
            raise TypeError("_transport_list_reader must satisfy AdbTransportListReader")
        self._transport_list_snapshot_epoch_issuer = transport_list_snapshot_epoch_issuer
        self._transport_list_reader = _transport_list_reader

    def read(self, endpoint: TcpAddress) -> AdbTransportListSnapshot:
        if not isinstance(endpoint, TcpAddress):
            raise TypeError("endpoint must be TcpAddress")
        return AdbTransportListSnapshot(
            observations=self._transport_list_reader.read(endpoint),
            epoch=self._transport_list_snapshot_epoch_issuer.issue(),
        )


__all__ = ["AdbTransportListSnapshotReader", "SmartSocketAdbTransportListSnapshotReader"]
