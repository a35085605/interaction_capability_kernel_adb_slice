from __future__ import annotations

from dataclasses import dataclass

from adb.server.identity import AdbServerIdentity
from adb.transport_list.identity import AdbTransportListIdentity
from adb.transport_list.model import AdbTransportList


@dataclass(frozen=True, slots=True)
class AdbTransportListObservationBasis:
    """Authoritative identities captured before one transport-list observation was read."""

    server: AdbServerIdentity
    transport_list_identity: AdbTransportListIdentity | None

    def __post_init__(self) -> None:
        if not isinstance(self.server, AdbServerIdentity):
            raise TypeError("server must be AdbServerIdentity")
        if self.transport_list_identity is not None and not isinstance(
            self.transport_list_identity, AdbTransportListIdentity
        ):
            raise TypeError(
                "transport_list_identity must be AdbTransportListIdentity or None"
            )


@dataclass(frozen=True, slots=True)
class AdbTransportListObservation:
    """One committed transport-list observation together with its production basis."""

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
    def server(self) -> AdbServerIdentity:
        """Server provenance captured before the observation was read."""

        return self.basis.server


__all__ = [
    "AdbTransportListObservation",
    "AdbTransportListObservationBasis",
]
