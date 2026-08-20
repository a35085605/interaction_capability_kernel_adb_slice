from __future__ import annotations

from collections.abc import Callable
from typing import Protocol, TypeVar, runtime_checkable

from eventing.models import EventSubscriptionToken


EventT = TypeVar("EventT")


@runtime_checkable
class EventPublisher(Protocol):
    """Publish immutable data events without owning their behavioral semantics."""

    def publish(self, event: object) -> None: ...


@runtime_checkable
class EventSubscriber(Protocol):
    """Register ordered handlers for event payload types."""

    def subscribe(
        self,
        event_type: type[EventT],
        handler: Callable[[EventT], None],
    ) -> EventSubscriptionToken: ...

    def unsubscribe(self, token: EventSubscriptionToken) -> bool: ...


class EventBus(EventPublisher, EventSubscriber, Protocol):
    """Combined event publication and subscription contract."""


__all__ = ["EventBus", "EventPublisher", "EventSubscriber"]
