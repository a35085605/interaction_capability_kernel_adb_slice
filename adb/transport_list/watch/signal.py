from __future__ import annotations

from dataclasses import dataclass
from uuid import uuid4

from adb.transport_list.watch.failure import AdbTransportListWatchFailure
from adb.transport_list.watch.generation import AdbTransportListWatchGeneration


def _require_generation(value: object) -> AdbTransportListWatchGeneration:
    if not isinstance(value, AdbTransportListWatchGeneration):
        raise TypeError("generation must be AdbTransportListWatchGeneration")
    return value


@dataclass(frozen=True, slots=True, order=True)
class AdbTransportListWatchRecoveryId:
    """Opaque identity for one runtime-supervised transport-list watch recovery."""

    value: str

    def __post_init__(self) -> None:
        if not isinstance(self.value, str):
            raise TypeError(
                "ADB transport-list watch recovery id must be a string, "
                f"got {type(self.value).__name__}"
            )
        normalized = self.value.strip()
        if not normalized:
            raise ValueError("ADB transport-list watch recovery id cannot be empty")
        object.__setattr__(self, "value", normalized)

    @classmethod
    def new(cls) -> "AdbTransportListWatchRecoveryId":
        return cls(uuid4().hex)


@dataclass(frozen=True, slots=True)
class AdbTransportListWatchFailed:
    """Signal a transport-list watch failure for one watch generation."""

    generation: AdbTransportListWatchGeneration
    failure: AdbTransportListWatchFailure

    def __post_init__(self) -> None:
        _require_generation(self.generation)
        if not isinstance(self.failure, AdbTransportListWatchFailure):
            raise TypeError("failure must be AdbTransportListWatchFailure")


@dataclass(frozen=True, slots=True)
class AdbTransportListWatchRecoveryRetryDue:
    """Runtime-supervision signal that one scheduled watch recovery attempt became due."""

    recovery_id: AdbTransportListWatchRecoveryId
    attempt_number: int

    def __post_init__(self) -> None:
        if not isinstance(self.recovery_id, AdbTransportListWatchRecoveryId):
            raise TypeError("recovery_id must be AdbTransportListWatchRecoveryId")
        if isinstance(self.attempt_number, bool) or not isinstance(self.attempt_number, int):
            raise TypeError("attempt_number must be an integer")
        if self.attempt_number <= 0:
            raise ValueError("attempt_number must be greater than zero")


__all__ = [
    "AdbTransportListWatchFailed",
    "AdbTransportListWatchFailure",
    "AdbTransportListWatchRecoveryId",
    "AdbTransportListWatchRecoveryRetryDue",
]
