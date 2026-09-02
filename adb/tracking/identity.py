from __future__ import annotations

from dataclasses import dataclass, field

from adb.epoch import Epoch


class _AdbTransportListEpoch(Epoch):
    """Internal ordinal backing one committed transport-list identity."""

    __slots__ = ()


@dataclass(frozen=True, slots=True)
class AdbTransportListIdentity:
    """Runtime-scoped identity of one committed authoritative transport-list revision."""

    _epoch: _AdbTransportListEpoch = field(repr=False)

    def __post_init__(self) -> None:
        if not isinstance(self._epoch, _AdbTransportListEpoch):
            raise TypeError("_epoch must be _AdbTransportListEpoch")

    def __str__(self) -> str:
        return str(self._epoch)


class AdbTransportListIdentityIssuer:
    """Issue the initial and direct successor identities for committed list revisions."""

    __slots__ = ()

    def initial(self) -> AdbTransportListIdentity:
        """Return the identity of the first committed transport-list revision."""

        return AdbTransportListIdentity(_AdbTransportListEpoch(1))

    def successor(self, previous: AdbTransportListIdentity) -> AdbTransportListIdentity:
        """Return the direct successor of ``previous`` within the same runtime scope."""

        if not isinstance(previous, AdbTransportListIdentity):
            raise TypeError("previous must be AdbTransportListIdentity")
        return AdbTransportListIdentity(_AdbTransportListEpoch(previous._epoch.value + 1))


__all__ = ["AdbTransportListIdentity", "AdbTransportListIdentityIssuer"]
