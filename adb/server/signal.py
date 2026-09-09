from __future__ import annotations

from dataclasses import dataclass
from uuid import uuid4


@dataclass(frozen=True, slots=True, order=True)
class AdbServerRecoveryId:
    """Opaque identity for one runtime-supervised server recovery."""

    value: str

    def __post_init__(self) -> None:
        if not isinstance(self.value, str):
            raise TypeError(
                "ADB server recovery id must be a string, "
                f"got {type(self.value).__name__}"
            )
        normalized = self.value.strip()
        if not normalized:
            raise ValueError("ADB server recovery id cannot be empty")
        object.__setattr__(self, "value", normalized)

    @classmethod
    def new(cls) -> "AdbServerRecoveryId":
        return cls(uuid4().hex)


@dataclass(frozen=True, slots=True)
class AdbServerRecoveryRetryDue:
    """Runtime-supervision signal that one scheduled recovery acquisition attempt became due."""

    recovery_id: AdbServerRecoveryId
    attempt_number: int

    def __post_init__(self) -> None:
        if not isinstance(self.recovery_id, AdbServerRecoveryId):
            raise TypeError("recovery_id must be AdbServerRecoveryId")
        if isinstance(self.attempt_number, bool) or not isinstance(self.attempt_number, int):
            raise TypeError("attempt_number must be an integer")
        if self.attempt_number <= 0:
            raise ValueError("attempt_number must be greater than zero")


__all__ = [
    "AdbServerRecoveryId",
    "AdbServerRecoveryRetryDue",
]
