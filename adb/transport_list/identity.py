from __future__ import annotations

from dataclasses import dataclass, field

from adb.epoch import Epoch, EpochSequence


class _AdbTransportListEpoch(Epoch):
    """Internal ordinal backing one transport-list revision identity."""

    __slots__ = ()


@dataclass(frozen=True, slots=True)
class AdbTransportListIdentity:
    """Runtime-scoped identity of one logical transport-list revision.

    Identity alone does not imply that the identified revision became authoritative.
    """

    _epoch: _AdbTransportListEpoch = field(repr=False)

    def __post_init__(self) -> None:
        if not isinstance(self._epoch, _AdbTransportListEpoch):
            raise TypeError("_epoch must be _AdbTransportListEpoch")

    def __str__(self) -> str:
        return str(self._epoch)


class AdbTransportListIdentityIssuer:
    """Issue monotonically increasing identities within one ADB runtime scope."""

    __slots__ = ("_sequence",)

    def __init__(self, *, after: AdbTransportListIdentity | None = None) -> None:
        if after is not None and not isinstance(after, AdbTransportListIdentity):
            raise TypeError("after must be AdbTransportListIdentity or None")
        initial_value = 0 if after is None else after._epoch.value
        self._sequence = EpochSequence(_AdbTransportListEpoch, initial_value=initial_value)

    def issue(self) -> AdbTransportListIdentity:
        """Issue one fresh transport-list revision identity."""

        return AdbTransportListIdentity(self._sequence.issue())


__all__ = ["AdbTransportListIdentity", "AdbTransportListIdentityIssuer"]
