from __future__ import annotations

from dataclasses import dataclass
from threading import Lock
from typing import Protocol, runtime_checkable


def _normalize_diagnostic(value: object) -> str:
    if not isinstance(value, str):
        raise TypeError("diagnostic must be a string")
    normalized = value.strip()
    if not normalized:
        raise ValueError("diagnostic cannot be empty")
    return normalized


@dataclass(frozen=True, slots=True)
class CleanupHandoff:
    """Ownership of a physical resource whose normal cleanup could not be confirmed."""

    resource: object
    diagnostic: str

    def __post_init__(self) -> None:
        if self.resource is None:
            raise TypeError("resource cannot be None")
        object.__setattr__(self, "diagnostic", _normalize_diagnostic(self.diagnostic))


class CleanupHandoffError(RuntimeError):
    """Carry a primary adapter failure together with unresolved cleanup ownership."""

    def __init__(self, primary_error: BaseException, cleanup_handoff: CleanupHandoff) -> None:
        if not isinstance(primary_error, BaseException):
            raise TypeError("primary_error must be BaseException")
        if not isinstance(cleanup_handoff, CleanupHandoff):
            raise TypeError("cleanup_handoff must be CleanupHandoff")
        self.primary_error = primary_error
        self.cleanup_handoff = cleanup_handoff
        super().__init__(
            f"{primary_error}; unresolved cleanup ownership requires sink acceptance: "
            f"{cleanup_handoff.diagnostic}"
        )


@runtime_checkable
class CleanupSink(Protocol):
    """Accept ownership of resources abandoned by normal backend cleanup."""

    def accept(self, cleanup_handoff: CleanupHandoff) -> None:
        """Consume cleanup ownership; callers never reclaim it, including if this call raises."""
        ...


class RetainingCleanupSink:
    """Minimal sink that retains accepted cleanup ownership indefinitely."""

    def __init__(self) -> None:
        self._lock = Lock()
        self._retained: list[CleanupHandoff] = []

    def accept(self, cleanup_handoff: CleanupHandoff) -> None:
        if not isinstance(cleanup_handoff, CleanupHandoff):
            raise TypeError("cleanup_handoff must be CleanupHandoff")
        with self._lock:
            self._retained.append(cleanup_handoff)

    def retained(self) -> tuple[CleanupHandoff, ...]:
        with self._lock:
            return tuple(self._retained)


__all__ = [
    "CleanupHandoff",
    "CleanupHandoffError",
    "CleanupSink",
    "RetainingCleanupSink",
]
