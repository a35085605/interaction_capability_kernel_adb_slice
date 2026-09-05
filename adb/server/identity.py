from __future__ import annotations

from dataclasses import dataclass, field

from adb.epoch import Epoch, EpochSequence


class _AdbServerEpoch(Epoch):
    """Internal ordinal backing one runtime-scoped ADB server identity."""

    __slots__ = ()


@dataclass(frozen=True, slots=True)
class AdbServerIdentity:
    """Runtime-scoped identity for an ADB server occurrence.

    Server authority and process ownership are tracked separately.
    """

    _epoch: _AdbServerEpoch = field(repr=False)

    def __post_init__(self) -> None:
        if not isinstance(self._epoch, _AdbServerEpoch):
            raise TypeError("_epoch must be _AdbServerEpoch")

    def __str__(self) -> str:
        return str(self._epoch)


class AdbServerIdentityIssuer:
    """Issue monotonically increasing identities within one ADB runtime scope."""

    __slots__ = ("_sequence",)

    def __init__(self, *, after: AdbServerIdentity | None = None) -> None:
        if after is not None and not isinstance(after, AdbServerIdentity):
            raise TypeError("after must be AdbServerIdentity or None")
        initial_value = 0 if after is None else after._epoch.value
        self._sequence = EpochSequence(_AdbServerEpoch, initial_value=initial_value)

    def issue(self) -> AdbServerIdentity:
        """Issue a fresh server identity."""

        return AdbServerIdentity(self._sequence.issue())


__all__ = ["AdbServerIdentity", "AdbServerIdentityIssuer"]
