from __future__ import annotations

from dataclasses import dataclass

from networking import TcpAddress
from adb.server.endpoint import AdbServerEndpoint
from adb.server.epoch import AdbServerEpoch
from adb.server.identity import AdbServerIdentity


@dataclass(frozen=True, slots=True)
class AdbServerLifetime:
    """Immutable pairing of one server identity with its connection endpoint."""

    endpoint: AdbServerEndpoint
    identity: AdbServerIdentity

    def __post_init__(self) -> None:
        if not isinstance(self.endpoint, TcpAddress):
            raise TypeError("endpoint must be TcpAddress")
        if not isinstance(self.identity, AdbServerIdentity):
            raise TypeError("identity must be AdbServerIdentity")

    @property
    def epoch(self) -> AdbServerEpoch:
        """Project the monotonic ordinal carried by this server identity."""

        return self.identity.epoch


__all__ = ["AdbServerLifetime"]
