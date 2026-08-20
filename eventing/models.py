from __future__ import annotations

from dataclasses import dataclass


def _normalize_required_text(value: object, *, field_name: str) -> str:
    if not isinstance(value, str):
        raise TypeError(f"{field_name} must be a string")
    normalized = value.strip()
    if not normalized:
        raise ValueError(f"{field_name} cannot be empty")
    return normalized


@dataclass(frozen=True, slots=True, order=True)
class EventSubscriptionToken:
    """Opaque identity for one event-bus subscription registration."""

    value: str

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "value",
            _normalize_required_text(self.value, field_name="event subscription token"),
        )


@dataclass(frozen=True, slots=True)
class EventHandlerFailure:
    """One subscriber failure captured while dispatching an event."""

    event: object
    handler: object
    error: BaseException

    def __post_init__(self) -> None:
        if not callable(self.handler):
            raise TypeError("handler must be callable")
        if not isinstance(self.error, BaseException):
            raise TypeError("error must be BaseException")


class EventDispatchError(RuntimeError):
    """Raised after queued delivery completes when one or more handlers failed."""

    def __init__(self, failures: tuple[EventHandlerFailure, ...]) -> None:
        if not failures:
            raise ValueError("EventDispatchError requires at least one failure")
        if not all(isinstance(failure, EventHandlerFailure) for failure in failures):
            raise TypeError("failures must contain EventHandlerFailure values")
        self.failures = failures
        super().__init__(f"{len(failures)} event handler failure(s) occurred")


__all__ = [
    "EventDispatchError",
    "EventHandlerFailure",
    "EventSubscriptionToken",
]
