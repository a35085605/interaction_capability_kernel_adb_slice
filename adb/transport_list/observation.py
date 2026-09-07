from __future__ import annotations

from dataclasses import dataclass

from adb.transport_list.identity import AdbTransportListIdentity
from adb.transport_list.model import AdbTransportList
from adb.transport_list.session_identity import AdbTransportListSessionIdentity


@dataclass(frozen=True, slots=True)
class AdbTransportListObservation:
    """One committed transport-list observation and its producer session."""

    session: AdbTransportListSessionIdentity
    identity: AdbTransportListIdentity
    transport_list: AdbTransportList

    def __post_init__(self) -> None:
        if not isinstance(self.session, AdbTransportListSessionIdentity):
            raise TypeError("session must be AdbTransportListSessionIdentity")
        if not isinstance(self.identity, AdbTransportListIdentity):
            raise TypeError("identity must be AdbTransportListIdentity")
        if not isinstance(self.transport_list, AdbTransportList):
            raise TypeError("transport_list must be AdbTransportList")

    @property
    def session_identity(self) -> AdbTransportListSessionIdentity:
        return self.session


__all__ = ["AdbTransportListObservation"]
