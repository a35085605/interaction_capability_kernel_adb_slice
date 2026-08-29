from __future__ import annotations

from dataclasses import dataclass

from adb.epoch import Epoch, EpochSequence
from adb.server.address import AdbServerAddress


class ServerEpoch(Epoch):
    """Ordinal identity for successive ADB server lifetimes within one runtime."""

    __slots__ = ()


class ServerEpochSequence(EpochSequence[ServerEpoch]):
    """Runtime-scoped monotonically increasing ADB server epoch issuer."""

    def __init__(self) -> None:
        super().__init__(ServerEpoch)


@dataclass(frozen=True, slots=True)
class AdbServer:
    """Identity for one ADB server lifetime.

    The epoch distinguishes successive server lifetimes.
    """

    endpoint: AdbServerAddress
    epoch: ServerEpoch

    def __post_init__(self) -> None:
        if not isinstance(self.endpoint, AdbServerAddress):
            raise TypeError("endpoint must be AdbServerAddress")
        if not isinstance(self.epoch, ServerEpoch):
            raise TypeError("epoch must be ServerEpoch")


__all__ = ["AdbServer", "ServerEpoch", "ServerEpochSequence"]
