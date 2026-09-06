from __future__ import annotations

from dataclasses import dataclass

from adb.server.identity import AdbServerIdentity
from adb.transport_list.identity import (
    AdbTransportListIdentity,
    AdbTransportListIdentityIssuer,
)
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
    """One complete transport-list observation together with its production basis."""

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
        """Server provenance retained as a compatibility projection of :attr:`basis`."""

        return self.basis.server


class AdbTransportListObservationIdentifier:
    """Identify raw transport-list values exactly once at the runtime observation boundary."""

    __slots__ = ("_identity_issuer",)

    def __init__(self, *, after: AdbTransportListIdentity | None = None) -> None:
        self._identity_issuer = AdbTransportListIdentityIssuer(after=after)

    def identify(
        self,
        basis: AdbTransportListObservationBasis,
        transport_list: AdbTransportList,
    ) -> AdbTransportListObservation:
        """Bind raw content to a captured basis and a fresh runtime-scoped identity."""

        if not isinstance(basis, AdbTransportListObservationBasis):
            raise TypeError("basis must be AdbTransportListObservationBasis")
        basis_identity = basis.transport_list_identity
        if basis_identity is not None and not self._identity_issuer.owns(basis_identity):
            raise ValueError(
                "basis transport-list identity belongs to a different runtime scope"
            )
        if not isinstance(transport_list, AdbTransportList):
            raise TypeError("transport_list must be AdbTransportList")
        return AdbTransportListObservation(
            basis=basis,
            identity=self._identity_issuer.issue(),
            transport_list=transport_list,
        )

    def owns(self, observation: AdbTransportListObservation) -> bool:
        """Whether an observation identity belongs to this runtime identifier."""

        if not isinstance(observation, AdbTransportListObservation):
            raise TypeError("observation must be AdbTransportListObservation")
        return self._identity_issuer.owns(observation.identity)


__all__ = [
    "AdbTransportListObservation",
    "AdbTransportListObservationBasis",
    "AdbTransportListObservationIdentifier",
]
