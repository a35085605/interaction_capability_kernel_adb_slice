from __future__ import annotations

from dataclasses import dataclass
from threading import Lock
from typing import Generic, Protocol, TypeVar, runtime_checkable


@dataclass(frozen=True, slots=True, order=True)
class Epoch:
    """Strongly typed monotonic lifetime ordinal."""

    value: int

    def __post_init__(self) -> None:
        if isinstance(self.value, bool) or not isinstance(self.value, int):
            raise TypeError("epoch value must be an integer")
        if self.value <= 0:
            raise ValueError("epoch value must be greater than zero")

    def __str__(self) -> str:
        return str(self.value)


EpochT = TypeVar("EpochT", bound=Epoch)


@runtime_checkable
class EpochIssuer(Protocol[EpochT]):
    """Issue monotonically increasing epochs within one ownership scope."""

    def issue(self) -> EpochT:
        ...


class EpochSequence(Generic[EpochT]):
    """Thread-safe monotonically increasing issuer for one concrete epoch type."""

    def __init__(self, epoch_type: type[EpochT]) -> None:
        if not isinstance(epoch_type, type) or not issubclass(epoch_type, Epoch):
            raise TypeError("epoch_type must be an Epoch subclass")
        self._epoch_type = epoch_type
        self._lock = Lock()
        self._current = 0

    def issue(self) -> EpochT:
        with self._lock:
            self._current += 1
            return self._epoch_type(self._current)


__all__ = ["Epoch", "EpochIssuer", "EpochSequence"]
