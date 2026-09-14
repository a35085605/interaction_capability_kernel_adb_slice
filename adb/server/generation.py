from __future__ import annotations

from dataclasses import dataclass, field

from adb._generation import EpochBackedGenerationIssuer
from adb.epoch import Epoch


class _AdbServerGenerationEpoch(Epoch):
    __slots__ = ()


@dataclass(frozen=True, slots=True)
class AdbServerGeneration:
    """Identify one ADB server lifecycle generation."""

    _epoch: _AdbServerGenerationEpoch = field(repr=False)

    def __post_init__(self) -> None:
        if not isinstance(self._epoch, _AdbServerGenerationEpoch):
            raise TypeError("_epoch must be _AdbServerGenerationEpoch")

    def __str__(self) -> str:
        return str(self._epoch)


class AdbServerGenerationIssuer:
    """Issue monotonically increasing ADB server generations."""

    __slots__ = ("_issuer",)

    def __init__(self, *, after: AdbServerGeneration | None = None) -> None:
        if after is not None and not isinstance(after, AdbServerGeneration):
            raise TypeError("after must be AdbServerGeneration or None")
        self._issuer = EpochBackedGenerationIssuer(
            _AdbServerGenerationEpoch,
            AdbServerGeneration,
            after_epoch=None if after is None else after._epoch,
        )

    def issue(self) -> AdbServerGeneration:
        """Issue a generation newer than every generation previously issued here."""

        return self._issuer.issue()


__all__ = ["AdbServerGeneration", "AdbServerGenerationIssuer"]
