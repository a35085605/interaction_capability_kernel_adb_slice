from __future__ import annotations

from collections.abc import Iterable
from typing import Protocol, runtime_checkable

from adb.adapters.aosp.track_devices import SmartSocketAdbTransportListReader
from adb.transport.model import AdbTransport
from adb.transport_list.model import AdbTransportList
from networking import TcpAddress


@runtime_checkable
class _AdbTransportListSourceReader(Protocol):
    """Read transports from one ADB server endpoint for snapshot normalization."""

    def read(self, address: TcpAddress) -> Iterable[AdbTransport]:
        ...


class AdbTransportListSnapshotReader(Protocol):
    """Read one complete current domain transport-list snapshot."""

    def read(self, endpoint: TcpAddress) -> AdbTransportList:
        ...


class SmartSocketAdbTransportListSnapshotReader:
    """Normalize one smart-socket transport list into a domain snapshot value."""

    def __init__(
        self,
        *,
        _transport_list_reader: _AdbTransportListSourceReader | None = None,
    ) -> None:
        if _transport_list_reader is None:
            _transport_list_reader = SmartSocketAdbTransportListReader()
        if not isinstance(_transport_list_reader, _AdbTransportListSourceReader):
            raise TypeError(
                "_transport_list_reader must provide read(address) transport iteration"
            )
        self._transport_list_reader = _transport_list_reader

    def read(self, endpoint: TcpAddress) -> AdbTransportList:
        if not isinstance(endpoint, TcpAddress):
            raise TypeError("endpoint must be TcpAddress")
        return AdbTransportList(
            transports=self._transport_list_reader.read(endpoint),
        )


__all__ = [
    "AdbTransportListSnapshotReader",
    "SmartSocketAdbTransportListSnapshotReader",
]
