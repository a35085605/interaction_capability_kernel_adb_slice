from __future__ import annotations

from dataclasses import dataclass, field

from adb._generation import EpochBackedGenerationIssuer
from adb.epoch import Epoch


class _AdbTransportListRevisionEpoch(Epoch):
    """Internal ordinal backing one transport-list projection revision."""

    __slots__ = ()


@dataclass(frozen=True, slots=True)
class AdbTransportListRevision:
    """Revision of one transport-list projection.

    Revisions are meaningful only within the state authority that issued the revision sequence.
    They carry no watch or server lifecycle meaning. A state store advances its revision whenever
    its visible projection changes.
    """

    _epoch: _AdbTransportListRevisionEpoch = field(repr=False)

    def __post_init__(self) -> None:
        if not isinstance(self._epoch, _AdbTransportListRevisionEpoch):
            raise TypeError("_epoch must be _AdbTransportListRevisionEpoch")

    def __str__(self) -> str:
        return str(self._epoch)


class AdbTransportListRevisionIssuer:
    """Issue monotonically increasing revisions for one transport-list state authority."""

    __slots__ = ("_issuer",)

    def __init__(self, *, after: AdbTransportListRevision | None = None) -> None:
        if after is not None and not isinstance(after, AdbTransportListRevision):
            raise TypeError("after must be AdbTransportListRevision or None")
        self._issuer = EpochBackedGenerationIssuer(
            _AdbTransportListRevisionEpoch,
            AdbTransportListRevision,
            after_epoch=None if after is None else after._epoch,
        )

    def issue(self) -> AdbTransportListRevision:
        """Issue a fresh transport-list state revision."""

        return self._issuer.issue()


__all__ = ["AdbTransportListRevision", "AdbTransportListRevisionIssuer"]
