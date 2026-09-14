from __future__ import annotations

from dataclasses import dataclass, field

from adb._generation import EpochBackedGenerationIssuer
from adb.epoch import Epoch


class _AdbTransportListGenerationEpoch(Epoch):
    """Internal ordinal backing one transport-list projection generation."""

    __slots__ = ()


@dataclass(frozen=True, slots=True)
class AdbTransportListGeneration:
    """Generation of one transport-list projection revision.

    Generations are meaningful only within the state authority that issued the revision sequence.
    They carry no watch or server lifecycle meaning. A state store advances its generation whenever
    its visible projection changes.
    """

    _epoch: _AdbTransportListGenerationEpoch = field(repr=False)

    def __post_init__(self) -> None:
        if not isinstance(self._epoch, _AdbTransportListGenerationEpoch):
            raise TypeError("_epoch must be _AdbTransportListGenerationEpoch")

    def __str__(self) -> str:
        return str(self._epoch)


class AdbTransportListGenerationIssuer:
    """Issue monotonically increasing generations for one transport-list state authority."""

    __slots__ = ("_issuer",)

    def __init__(self, *, after: AdbTransportListGeneration | None = None) -> None:
        if after is not None and not isinstance(after, AdbTransportListGeneration):
            raise TypeError("after must be AdbTransportListGeneration or None")
        self._issuer = EpochBackedGenerationIssuer(
            _AdbTransportListGenerationEpoch,
            AdbTransportListGeneration,
            after_epoch=None if after is None else after._epoch,
        )

    def issue(self) -> AdbTransportListGeneration:
        """Issue a fresh transport-list state generation."""

        return self._issuer.issue()


__all__ = ["AdbTransportListGeneration", "AdbTransportListGenerationIssuer"]
