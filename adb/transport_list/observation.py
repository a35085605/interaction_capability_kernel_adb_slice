from __future__ import annotations

from dataclasses import dataclass

from adb.transport_list.identity import AdbTransportListIdentity
from adb.transport_list.model import AdbTransportList
from adb.transport_list.session_identity import AdbTransportListSessionIdentity


@dataclass(frozen=True, slots=True)
class AdbTransportListObservationBasis:
    """Session identity and list identity captured before one raw observation read."""

    session: AdbTransportListSessionIdentity
    transport_list_identity: AdbTransportListIdentity | None

    def __post_init__(self) -> None:
        if not isinstance(self.session, AdbTransportListSessionIdentity):
            raise TypeError("session must be AdbTransportListSessionIdentity")
        if self.transport_list_identity is not None and not isinstance(
            self.transport_list_identity, AdbTransportListIdentity
        ):
            raise TypeError(
                "transport_list_identity must be AdbTransportListIdentity or None"
            )

    @property
    def session_identity(self) -> AdbTransportListSessionIdentity:
        """Explicit alias used by watch/session code."""

        return self.session


@dataclass(frozen=True, slots=True)
class AdbTransportListObservation:
    """One committed transport-list observation together with its session-fenced basis."""

    basis: AdbTransportListObservationBasis
    identity: AdbTransportListIdentity
    transport_list: AdbTransportList

    def __post_init__(self) -> None:
        if not isinstance(self.basis, AdbTransportListObservationBasis):
            raise TypeError("basis must be AdbTransportListObservationBasis")
        if not isinstance(self.identity, AdbTransportListIdentity):
            raise TypeError("identity must be AdbTransportListIdentity")
        if not isinstance(self.transport_list, AdbTransportList):
            raise TypeError("transport_list must be AdbTransportList")

    @property
    def session(self) -> AdbTransportListSessionIdentity:
        """Producer session that committed this observation."""

        return self.basis.session

    @property
    def session_identity(self) -> AdbTransportListSessionIdentity:
        return self.basis.session


__all__ = [
    "AdbTransportListObservation",
    "AdbTransportListObservationBasis",
]
