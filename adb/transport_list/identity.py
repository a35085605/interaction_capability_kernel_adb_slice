from __future__ import annotations

from dataclasses import dataclass, field

from adb.epoch import Epoch, EpochSequence


class _AdbTransportListEpoch(Epoch):
    """Internal ordinal backing one runtime-scoped transport-list observation identity."""

    __slots__ = ()


class _AdbTransportListIdentityScope:
    """Opaque runtime-local namespace for transport-list observation identities."""

    __slots__ = ()


@dataclass(frozen=True, slots=True)
class AdbTransportListIdentity:
    """Runtime-scoped identity for one transport-list observation.

    Observation authority is tracked separately by transport-list state. The opaque scope
    prevents equal ordinals issued by different runtimes from comparing as the same identity.
    """

    _scope: _AdbTransportListIdentityScope = field(repr=False)
    _epoch: _AdbTransportListEpoch = field(repr=False)

    def __post_init__(self) -> None:
        if not isinstance(self._scope, _AdbTransportListIdentityScope):
            raise TypeError("_scope must be _AdbTransportListIdentityScope")
        if not isinstance(self._epoch, _AdbTransportListEpoch):
            raise TypeError("_epoch must be _AdbTransportListEpoch")

    def __str__(self) -> str:
        return str(self._epoch)


class AdbTransportListIdentityIssuer:
    """Issue monotonically increasing identities within one ADB runtime scope."""

    __slots__ = ("_scope", "_sequence")

    def __init__(self, *, after: AdbTransportListIdentity | None = None) -> None:
        if after is not None and not isinstance(after, AdbTransportListIdentity):
            raise TypeError("after must be AdbTransportListIdentity or None")
        self._scope = (
            _AdbTransportListIdentityScope() if after is None else after._scope
        )
        initial_value = 0 if after is None else after._epoch.value
        self._sequence = EpochSequence(_AdbTransportListEpoch, initial_value=initial_value)

    def issue(self) -> AdbTransportListIdentity:
        """Issue a fresh transport-list observation identity."""

        return AdbTransportListIdentity(self._scope, self._sequence.issue())

    def owns(self, identity: AdbTransportListIdentity) -> bool:
        """Whether ``identity`` belongs to this issuer's runtime-local namespace."""

        if not isinstance(identity, AdbTransportListIdentity):
            raise TypeError("identity must be AdbTransportListIdentity")
        return identity._scope is self._scope


__all__ = ["AdbTransportListIdentity", "AdbTransportListIdentityIssuer"]
