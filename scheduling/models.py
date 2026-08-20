from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


def _normalize_non_empty_text(value: object, *, field_name: str) -> str:
    if not isinstance(value, str):
        raise TypeError(
            f"{field_name} must be a string, got {type(value).__name__}"
        )
    normalized = value.strip()
    if not normalized:
        raise ValueError(f"{field_name} cannot be empty")
    return normalized


class MisfirePolicy(Enum):
    """How a scheduler handles occurrences that became due while unavailable."""

    SKIP = "skip"
    FIRE_ONCE = "fire_once"
    CATCH_UP_ALL = "catch_up_all"


@dataclass(frozen=True, slots=True)
class ScheduleToken:
    """Opaque identity returned for one scheduler registration."""

    value: str

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "value",
            _normalize_non_empty_text(
                self.value,
                field_name="schedule token",
            ),
        )


__all__ = ["MisfirePolicy", "ScheduleToken"]
