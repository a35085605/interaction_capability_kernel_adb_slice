from __future__ import annotations

from dataclasses import dataclass
from typing import TypeAlias

from adb.transport_list.watch.failure import AdbTransportListWatchFailure
from adb.transport_list.watch.generation import AdbTransportListWatchGeneration


def _require_generation(value: object) -> AdbTransportListWatchGeneration:
    if not isinstance(value, AdbTransportListWatchGeneration):
        raise TypeError("generation must be AdbTransportListWatchGeneration")
    return value


@dataclass(frozen=True, slots=True)
class AdbTransportListWatchStarted:
    """Signal that one watch generation committed its initial transport list."""

    generation: AdbTransportListWatchGeneration

    def __post_init__(self) -> None:
        _require_generation(self.generation)


@dataclass(frozen=True, slots=True)
class AdbTransportListWatchStopped:
    """Signal that one watch generation ended and its authority was revoked."""

    generation: AdbTransportListWatchGeneration

    def __post_init__(self) -> None:
        _require_generation(self.generation)


@dataclass(frozen=True, slots=True)
class AdbTransportListWatchFailed:
    """Signal a transport-list watch failure for one watch generation."""

    generation: AdbTransportListWatchGeneration
    failure: AdbTransportListWatchFailure

    def __post_init__(self) -> None:
        _require_generation(self.generation)
        if not isinstance(self.failure, AdbTransportListWatchFailure):
            raise TypeError("failure must be AdbTransportListWatchFailure")


AdbTransportListWatchSignal: TypeAlias = (
    AdbTransportListWatchStarted
    | AdbTransportListWatchStopped
    | AdbTransportListWatchFailed
)


__all__ = [
    "AdbTransportListWatchFailed",
    "AdbTransportListWatchFailure",
    "AdbTransportListWatchSignal",
    "AdbTransportListWatchStarted",
    "AdbTransportListWatchStopped",
]
