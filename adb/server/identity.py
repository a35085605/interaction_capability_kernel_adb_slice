from __future__ import annotations

from dataclasses import dataclass

from adb.server.epoch import AdbServerEpoch


@dataclass(frozen=True, slots=True)
class AdbServerIdentity:
    """Runtime-scoped identity of one committed ADB server lifetime."""

    epoch: AdbServerEpoch

    def __post_init__(self) -> None:
        if not isinstance(self.epoch, AdbServerEpoch):
            raise TypeError("epoch must be AdbServerEpoch")


class AdbServerIdentityIssuer:
    """Issue the initial and direct successor identities for committed server lifetimes."""

    __slots__ = ()

    def initial(self) -> AdbServerIdentity:
        """Return the identity of the first committed server lifetime."""

        return AdbServerIdentity(AdbServerEpoch(1))

    def successor(self, previous: AdbServerIdentity) -> AdbServerIdentity:
        """Return the direct successor of ``previous`` within the same runtime scope."""

        if not isinstance(previous, AdbServerIdentity):
            raise TypeError("previous must be AdbServerIdentity")
        return AdbServerIdentity(AdbServerEpoch(previous.epoch.value + 1))


__all__ = ["AdbServerIdentity", "AdbServerIdentityIssuer"]
