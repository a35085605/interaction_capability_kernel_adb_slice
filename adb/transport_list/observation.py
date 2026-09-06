from __future__ import annotations

from dataclasses import dataclass

from adb.server.identity import AdbServerIdentity
from adb.transport_list.identity import (
    AdbTransportListIdentity,
    AdbTransportListIdentityIssuer,
)
from adb.transport_list.model import AdbTransportList


@dataclass(frozen=True, slots=True)
class AdbTransportListObservation:
    """One complete transport-list observation produced within an ADB runtime scope."""

    identity: AdbTransportListIdentity
    server: AdbServerIdentity
    transport_list: AdbTransportList

    def __post_init__(self) -> None:
        if not isinstance(self.identity, AdbTransportListIdentity):
            raise TypeError("identity must be AdbTransportListIdentity")
        if not isinstance(self.server, AdbServerIdentity):
            raise TypeError("server must be AdbServerIdentity")
        if not isinstance(self.transport_list, AdbTransportList):
            raise TypeError("transport_list must be AdbTransportList")


class AdbTransportListObservationIdentifier:
    """Identify raw transport-list values exactly once at the runtime observation boundary."""

    __slots__ = ("_identity_issuer",)

    def __init__(self, *, after: AdbTransportListIdentity | None = None) -> None:
        self._identity_issuer = AdbTransportListIdentityIssuer(after=after)

    def identify(
        self,
        server: AdbServerIdentity,
        transport_list: AdbTransportList,
    ) -> AdbTransportListObservation:
        """Bind raw content to its server provenance and a fresh runtime-scoped identity."""

        if not isinstance(server, AdbServerIdentity):
            raise TypeError("server must be AdbServerIdentity")
        if not isinstance(transport_list, AdbTransportList):
            raise TypeError("transport_list must be AdbTransportList")
        return AdbTransportListObservation(
            identity=self._identity_issuer.issue(),
            server=server,
            transport_list=transport_list,
        )

    def owns(self, observation: AdbTransportListObservation) -> bool:
        """Whether an observation identity belongs to this runtime identifier."""

        if not isinstance(observation, AdbTransportListObservation):
            raise TypeError("observation must be AdbTransportListObservation")
        return self._identity_issuer.owns(observation.identity)


__all__ = [
    "AdbTransportListObservation",
    "AdbTransportListObservationIdentifier",
]
