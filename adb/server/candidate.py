from __future__ import annotations

from dataclasses import dataclass

from networking import TcpAddress
from adb.server.endpoint import AdbServerEndpoint
from adb.server.identity import AdbServerIdentity


@dataclass(frozen=True, slots=True)
class AdbServerCandidate:
    """One usable ADB server endpoint paired with a runtime-scoped identity for authoritative arbitration."""

    identity: AdbServerIdentity
    endpoint: AdbServerEndpoint

    def __post_init__(self) -> None:
        if not isinstance(self.identity, AdbServerIdentity):
            raise TypeError("identity must be AdbServerIdentity")
        if not isinstance(self.endpoint, TcpAddress):
            raise TypeError("endpoint must be TcpAddress")


__all__ = ["AdbServerCandidate"]
