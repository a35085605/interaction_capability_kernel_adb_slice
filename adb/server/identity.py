from __future__ import annotations

from dataclasses import dataclass, field

from adb.epoch import Epoch


class _AdbServerEpoch(Epoch):
    """Internal ordinal backing one committed ADB server identity."""

    __slots__ = ()


@dataclass(frozen=True, slots=True)
class AdbServerIdentity:
    """Runtime-scoped identity of one committed ADB server lifetime."""

    _epoch: _AdbServerEpoch = field(repr=False)

    def __post_init__(self) -> None:
        if not isinstance(self._epoch, _AdbServerEpoch):
            raise TypeError("_epoch must be _AdbServerEpoch")

    def __str__(self) -> str:
        return str(self._epoch)


class AdbServerIdentityIssuer:
    """Issue the initial and direct successor identities for committed server lifetimes."""

    __slots__ = ()

    def initial(self) -> AdbServerIdentity:
        """Return the identity of the first committed server lifetime."""

        return AdbServerIdentity(_AdbServerEpoch(1))

    def successor(self, previous: AdbServerIdentity) -> AdbServerIdentity:
        """Return the direct successor of ``previous`` within the same runtime scope."""

        if not isinstance(previous, AdbServerIdentity):
            raise TypeError("previous must be AdbServerIdentity")
        return AdbServerIdentity(_AdbServerEpoch(previous._epoch.value + 1))


__all__ = ["AdbServerIdentity", "AdbServerIdentityIssuer"]
