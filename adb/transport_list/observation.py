from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from adb.transport_list.identity import AdbTransportListIdentity
from adb.transport_list.model import AdbTransportList

if TYPE_CHECKING:
    from adb.transport_list.watch.generation import AdbTransportListWatchGeneration


def _require_generation(value: object) -> AdbTransportListWatchGeneration:
    from adb.transport_list.watch.generation import AdbTransportListWatchGeneration

    if not isinstance(value, AdbTransportListWatchGeneration):
        raise TypeError("generation must be AdbTransportListWatchGeneration")
    return value


@dataclass(frozen=True, slots=True)
class AdbTransportListObservation:
    """One committed transport-list observation and its producing watch generation."""

    generation: AdbTransportListWatchGeneration
    identity: AdbTransportListIdentity
    transport_list: AdbTransportList

    def __post_init__(self) -> None:
        _require_generation(self.generation)
        if not isinstance(self.identity, AdbTransportListIdentity):
            raise TypeError("identity must be AdbTransportListIdentity")
        if not isinstance(self.transport_list, AdbTransportList):
            raise TypeError("transport_list must be AdbTransportList")


__all__ = ["AdbTransportListObservation"]
