from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from networking import TcpAddress
from adb.server.endpoint import AdbServerEndpoint
from adb.server.identity import AdbServerIdentity, AdbServerIdentityIssuer

if TYPE_CHECKING:
    from adb.server.state import AdbServerState


@dataclass(frozen=True, slots=True)
class AdbServerCandidate:
    """One server-activation proposal bound to the authority state it was based on."""

    identity: AdbServerIdentity
    endpoint: AdbServerEndpoint
    expected_state: AdbServerState

    def __post_init__(self) -> None:
        if not isinstance(self.identity, AdbServerIdentity):
            raise TypeError("identity must be AdbServerIdentity")
        if not isinstance(self.endpoint, TcpAddress):
            raise TypeError("endpoint must be TcpAddress")
        from adb.server.state import AdbServerState

        if not isinstance(self.expected_state, AdbServerState):
            raise TypeError("expected_state must be AdbServerState")


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
        expected_state: AdbServerState,
    ) -> AdbServerCandidate:
        """Create one candidate from a usable endpoint and its pre-acquisition state fence."""

        if not isinstance(endpoint, TcpAddress):
            raise TypeError("endpoint must be TcpAddress")
        from adb.server.state import AdbServerState

        if not isinstance(expected_state, AdbServerState):
            raise TypeError("expected_state must be AdbServerState")
        return AdbServerCandidate(
            identity=self._identity_issuer.issue(),
            endpoint=endpoint,
            expected_state=expected_state,
        )


__all__ = ["AdbServerCandidate", "AdbServerCandidateFactory"]
