from __future__ import annotations

from typing import Protocol

from adb.adapters.aosp.track_devices import SmartSocketAdbTransportListReader
from networking import TcpAddress
from adb.tracking.transport_list import AdbTransportListReader
from adb.tracking.snapshot.model import AdbTransportListSnapshot


class AdbTransportListSnapshotReader(Protocol):
    """Read one complete current domain transport-list snapshot."""

    def read(self, endpoint: TcpAddress) -> AdbTransportListSnapshot:
        ...


class SmartSocketAdbTransportListSnapshotReader:
    """Translate one smart-socket transport list into a domain snapshot value."""

    def __init__(
        self,
        *,
        _transport_list_reader: AdbTransportListReader | None = None,
    ) -> None:
        if _transport_list_reader is None:
            _transport_list_reader = SmartSocketAdbTransportListReader()
        if not isinstance(_transport_list_reader, AdbTransportListReader):
            raise TypeError("_transport_list_reader must satisfy AdbTransportListReader")
        self._transport_list_reader = _transport_list_reader

    def read(self, endpoint: TcpAddress) -> AdbTransportListSnapshot:
        if not isinstance(endpoint, TcpAddress):
            raise TypeError("endpoint must be TcpAddress")
        return AdbTransportListSnapshot(
            observations=self._transport_list_reader.read(endpoint),
        )


__all__ = ["AdbTransportListSnapshotReader", "SmartSocketAdbTransportListSnapshotReader"]
