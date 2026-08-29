from __future__ import annotations

from adb.epoch import Epoch, EpochSequence


class ServerEpoch(Epoch):
    """Runtime-scoped identity for one ADB server lifetime."""

    __slots__ = ()


class ServerEpochSequence(EpochSequence[ServerEpoch]):
    """Runtime-scoped monotonically increasing ADB server epoch issuer."""

    def __init__(self) -> None:
        super().__init__(ServerEpoch)


__all__ = ["ServerEpoch", "ServerEpochSequence"]
