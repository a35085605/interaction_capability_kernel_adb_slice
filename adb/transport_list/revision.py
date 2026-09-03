from __future__ import annotations

from dataclasses import dataclass

from adb.transport_list.identity import AdbTransportListIdentity
from adb.transport_list.model import AdbTransportList


@dataclass(frozen=True, slots=True)
class AdbTransportListRevision:
    """One observed transport list paired with a runtime-scoped identity for arbitration."""

    identity: AdbTransportListIdentity
    transport_list: AdbTransportList

    def __post_init__(self) -> None:
        if not isinstance(self.identity, AdbTransportListIdentity):
            raise TypeError("identity must be AdbTransportListIdentity")
        if not isinstance(self.transport_list, AdbTransportList):
            raise TypeError("transport_list must be AdbTransportList")


__all__ = ["AdbTransportListRevision"]
