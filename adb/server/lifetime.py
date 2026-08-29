from __future__ import annotations

from dataclasses import dataclass

from adb.server.address import AdbServerTcpAddress
from adb.server.epoch import ServerEpoch


@dataclass(frozen=True, slots=True)
class AdbServerLifetime:
    """Immutable association of one runtime server identity with its endpoint.

    ``epoch`` is the runtime-scoped lifetime identity. ``endpoint`` is the
    connection target used by infrastructure for that lifetime.
    """

    endpoint: AdbServerTcpAddress
    epoch: ServerEpoch

    def __post_init__(self) -> None:
        if not isinstance(self.endpoint, AdbServerTcpAddress):
            raise TypeError("endpoint must be AdbServerTcpAddress")
        if not isinstance(self.epoch, ServerEpoch):
            raise TypeError("epoch must be ServerEpoch")


__all__ = ["AdbServerLifetime"]
