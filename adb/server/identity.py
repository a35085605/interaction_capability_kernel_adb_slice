from __future__ import annotations

from dataclasses import dataclass, field

from adb.epoch import Epoch, EpochSequence


class _AdbServerEpoch(Epoch):
    """Internal ordinal backing one runtime-scoped ADB server identity."""

    __slots__ = ()


@dataclass(frozen=True, slots=True)
class AdbServerIdentity:
    """Runtime-scoped identity of one logical ADB server occurrence.

    Identity alone does not imply that the identified occurrence became authoritative, nor does it
    identify a physical adb server process lifetime.
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
        """Issue one fresh logical server-occurrence identity."""

        return AdbServerIdentity(self._sequence.issue())


__all__ = ["AdbServerIdentity", "AdbServerIdentityIssuer"]
