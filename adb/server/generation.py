from __future__ import annotations

from dataclasses import dataclass, field

from adb.epoch import Epoch, EpochSequence


class _AdbServerGenerationEpoch(Epoch):
    """Internal ordinal backing one runtime-scoped ADB server generation."""

    __slots__ = ()


@dataclass(frozen=True, slots=True)
class AdbServerGeneration:
    """Runtime-scoped generation fencing one ADB server authority lifetime.

    A lifecycle owns a current generation before any acquisition begins. Failed or retried
    acquisitions remain in that generation. Releasing or revoking matching authority advances
    the lifecycle to a fresh generation before physical cleanup, fencing stale lifecycle work.
    """

    _epoch: _AdbServerGenerationEpoch = field(repr=False)

    def __post_init__(self) -> None:
        if not isinstance(self._epoch, _AdbServerGenerationEpoch):
            raise TypeError("_epoch must be _AdbServerGenerationEpoch")

    def __str__(self) -> str:
        return str(self._epoch)


class AdbServerGenerationIssuer:
    """Issue monotonically increasing generations within one ADB runtime scope."""

    __slots__ = ("_sequence",)

    def __init__(self, *, after: AdbServerGeneration | None = None) -> None:
        if after is not None and not isinstance(after, AdbServerGeneration):
            raise TypeError("after must be AdbServerGeneration or None")
        initial_value = 0 if after is None else after._epoch.value
        self._sequence = EpochSequence(_AdbServerGenerationEpoch, initial_value=initial_value)

    def issue(self) -> AdbServerGeneration:
        """Issue a fresh server generation."""

        return AdbServerGeneration(self._sequence.issue())


__all__ = ["AdbServerGeneration", "AdbServerGenerationIssuer"]
