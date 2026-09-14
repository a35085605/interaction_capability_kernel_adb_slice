from __future__ import annotations

from dataclasses import dataclass, field

from adb._generation import EpochBackedGenerationIssuer
from adb.epoch import Epoch


class _AdbTransportListWatchGenerationEpoch(Epoch):
    """Internal ordinal backing one runtime-scoped transport-list watch generation."""

    __slots__ = ()


@dataclass(frozen=True, slots=True)
class AdbTransportListWatchGeneration:
    """Runtime-scoped generation fencing one transport-list watch authority lifetime.

    A lifecycle owns a current generation before acquisition begins. Failed or retried
    acquisitions remain in that generation until an explicit release completes. A matching
    release cleans up retained physical resources before issuing the next generation.
    """

    _epoch: _AdbTransportListWatchGenerationEpoch = field(repr=False)

    def __post_init__(self) -> None:
        if not isinstance(self._epoch, _AdbTransportListWatchGenerationEpoch):
            raise TypeError("_epoch must be _AdbTransportListWatchGenerationEpoch")

    def __str__(self) -> str:
        return str(self._epoch)


class AdbTransportListWatchGenerationIssuer:
    """Issue monotonically increasing watch generations within one ADB runtime scope."""

    __slots__ = ("_issuer",)

    def __init__(self, *, after: AdbTransportListWatchGeneration | None = None) -> None:
        if after is not None and not isinstance(after, AdbTransportListWatchGeneration):
            raise TypeError("after must be AdbTransportListWatchGeneration or None")
        self._issuer = EpochBackedGenerationIssuer(
            _AdbTransportListWatchGenerationEpoch,
            AdbTransportListWatchGeneration,
            after_epoch=None if after is None else after._epoch,
        )

    def issue(self) -> AdbTransportListWatchGeneration:
        """Issue a fresh transport-list watch generation."""

        return self._issuer.issue()


__all__ = [
    "AdbTransportListWatchGeneration",
    "AdbTransportListWatchGenerationIssuer",
]
