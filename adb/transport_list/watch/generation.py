from __future__ import annotations

from dataclasses import dataclass, field

from adb.epoch import Epoch, EpochSequence


class _AdbTransportListWatchGenerationEpoch(Epoch):
    """Internal ordinal backing one runtime-scoped transport-list watch generation."""

    __slots__ = ()


@dataclass(frozen=True, slots=True)
class AdbTransportListWatchGeneration:
    """Runtime-scoped generation fencing one transport-list watch authority lifetime.

    A backend owns a current generation before acquisition begins. Failed or retried
    acquisitions remain in that generation. Releasing matching pending or usable
    authority advances to a fresh generation before cancellation or physical cleanup.
    """

    _epoch: _AdbTransportListWatchGenerationEpoch = field(repr=False)

    def __post_init__(self) -> None:
        if not isinstance(self._epoch, _AdbTransportListWatchGenerationEpoch):
            raise TypeError("_epoch must be _AdbTransportListWatchGenerationEpoch")

    def __str__(self) -> str:
        return str(self._epoch)


class AdbTransportListWatchGenerationIssuer:
    """Issue monotonically increasing watch generations within one ADB runtime scope."""

    __slots__ = ("_sequence",)

    def __init__(self, *, after: AdbTransportListWatchGeneration | None = None) -> None:
        if after is not None and not isinstance(after, AdbTransportListWatchGeneration):
            raise TypeError("after must be AdbTransportListWatchGeneration or None")
        initial_value = 0 if after is None else after._epoch.value
        self._sequence = EpochSequence(
            _AdbTransportListWatchGenerationEpoch,
            initial_value=initial_value,
        )

    def issue(self) -> AdbTransportListWatchGeneration:
        """Issue a fresh transport-list watch generation."""

        return AdbTransportListWatchGeneration(self._sequence.issue())


__all__ = [
    "AdbTransportListWatchGeneration",
    "AdbTransportListWatchGenerationIssuer",
]
