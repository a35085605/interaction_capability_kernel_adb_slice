from __future__ import annotations

from dataclasses import dataclass

from adb.epoch import Epoch, EpochSequence


class AdbTransportListSessionEpoch(Epoch):
    """Monotonic ordinal for one transport-list watch session within a runtime."""

    __slots__ = ()


@dataclass(frozen=True, slots=True, eq=False)
class AdbTransportListSessionIdentity:
    """Opaque identity for one authoritative transport-list producer session.

    Object identity is the authority token. ``epoch`` is retained only as a monotonic runtime-local
    diagnostic ordinal and does not participate in equality.
    """

    epoch: AdbTransportListSessionEpoch

    def __post_init__(self) -> None:
        if not isinstance(self.epoch, AdbTransportListSessionEpoch):
            raise TypeError("epoch must be AdbTransportListSessionEpoch")

    def __str__(self) -> str:
        return str(self.epoch)


class AdbTransportListSessionIdentityIssuer:
    """Issue fresh runtime-scoped transport-list session identities."""

    __slots__ = ("_sequence",)

    def __init__(self) -> None:
        self._sequence = EpochSequence(AdbTransportListSessionEpoch)

    def issue(self) -> AdbTransportListSessionIdentity:
        return AdbTransportListSessionIdentity(self._sequence.issue())


__all__ = [
    "AdbTransportListSessionEpoch",
    "AdbTransportListSessionIdentity",
    "AdbTransportListSessionIdentityIssuer",
]
