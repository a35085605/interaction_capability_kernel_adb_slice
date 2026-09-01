from __future__ import annotations

from typing import Protocol, TypeAlias, runtime_checkable

from networking import TcpAddress
from adb.tracking.observation import AdbTrackedTransportObservation


AdbTransportList: TypeAlias = tuple[AdbTrackedTransportObservation, ...]


@runtime_checkable
class AdbTransportListReader(Protocol):
    """Read one complete current transport list from an ADB server endpoint."""

    def read(self, address: TcpAddress) -> AdbTransportList:
        ...


__all__ = ["AdbTransportList", "AdbTransportListReader"]
