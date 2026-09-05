from __future__ import annotations

from dataclasses import dataclass

from networking import TcpAddress
from adb.server.endpoint import AdbServerEndpoint
from adb.server.identity import AdbServerIdentity, AdbServerIdentityIssuer


@dataclass(frozen=True, slots=True)
class AdbServerCandidate:
    """One server-activation proposal bound to its authority-identity basis."""

    identity: AdbServerIdentity
    endpoint: AdbServerEndpoint
    base_identity: AdbServerIdentity | None

    def __post_init__(self) -> None:
        if not isinstance(self.identity, AdbServerIdentity):
            raise TypeError("identity must be AdbServerIdentity")
        if not isinstance(self.endpoint, TcpAddress):
            raise TypeError("endpoint must be TcpAddress")
        if self.base_identity is not None and not isinstance(
            self.base_identity, AdbServerIdentity
        ):
            raise TypeError("base_identity must be AdbServerIdentity or None")


class AdbServerCandidateFactory:
    """Materialize activation candidates without owning lifecycle transaction boundaries."""

    __slots__ = ("_identity_issuer",)

    def __init__(self, identity_issuer: AdbServerIdentityIssuer) -> None:
        if not isinstance(identity_issuer, AdbServerIdentityIssuer):
            raise TypeError("identity_issuer must be AdbServerIdentityIssuer")
        self._identity_issuer = identity_issuer

    def create(
        self,
        endpoint: AdbServerEndpoint,
        base_identity: AdbServerIdentity | None,
    ) -> AdbServerCandidate:
        """Create one candidate from a usable endpoint and its authority-identity basis."""

        if not isinstance(endpoint, TcpAddress):
            raise TypeError("endpoint must be TcpAddress")
        if base_identity is not None and not isinstance(base_identity, AdbServerIdentity):
            raise TypeError("base_identity must be AdbServerIdentity or None")
        return AdbServerCandidate(
            identity=self._identity_issuer.issue(),
            endpoint=endpoint,
            base_identity=base_identity,
        )


__all__ = ["AdbServerCandidate", "AdbServerCandidateFactory"]
