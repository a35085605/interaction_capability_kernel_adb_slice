from __future__ import annotations

from dataclasses import dataclass, field

from adb.epoch import Epoch, EpochSequence


class _AdbTransportListGenerationEpoch(Epoch):
    """Internal ordinal backing one transport-list projection generation."""

    __slots__ = ()


class _AdbTransportListGenerationScope:
    """Opaque namespace for generations issued by one transport-list authority."""

    __slots__ = ()


@dataclass(frozen=True, slots=True)
class AdbTransportListGeneration:
    """Generation of one authoritative transport-list projection state.

    The generation belongs only to the transport-list state capability. It carries no watch or
    server lifecycle meaning. A state store advances it whenever its visible projection changes.
    """

    _scope: _AdbTransportListGenerationScope = field(repr=False)
    _epoch: _AdbTransportListGenerationEpoch = field(repr=False)

    def __post_init__(self) -> None:
        if not isinstance(self._scope, _AdbTransportListGenerationScope):
            raise TypeError("_scope must be _AdbTransportListGenerationScope")
        if not isinstance(self._epoch, _AdbTransportListGenerationEpoch):
            raise TypeError("_epoch must be _AdbTransportListGenerationEpoch")

    def __str__(self) -> str:
        return str(self._epoch)


class AdbTransportListGenerationIssuer:
    """Issue monotonically increasing generations for one transport-list authority."""

    __slots__ = ("_scope", "_sequence")

    def __init__(self, *, after: AdbTransportListGeneration | None = None) -> None:
        if after is not None and not isinstance(after, AdbTransportListGeneration):
            raise TypeError("after must be AdbTransportListGeneration or None")
        self._scope = (
            _AdbTransportListGenerationScope() if after is None else after._scope
        )
        initial_value = 0 if after is None else after._epoch.value
        self._sequence = EpochSequence(
            _AdbTransportListGenerationEpoch,
            initial_value=initial_value,
        )

    def issue(self) -> AdbTransportListGeneration:
        """Issue a fresh transport-list state generation."""

        return AdbTransportListGeneration(self._scope, self._sequence.issue())

    def owns(self, generation: AdbTransportListGeneration) -> bool:
        """Whether ``generation`` belongs to this issuer's authority namespace."""

        if not isinstance(generation, AdbTransportListGeneration):
            raise TypeError("generation must be AdbTransportListGeneration")
        return generation._scope is self._scope


__all__ = ["AdbTransportListGeneration", "AdbTransportListGenerationIssuer"]
