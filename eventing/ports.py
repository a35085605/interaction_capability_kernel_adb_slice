from __future__ import annotations

from collections.abc import Callable
from typing import Protocol, TypeVar, runtime_checkable

from eventing.models import EventSubscriptionToken


EventT = TypeVar("EventT")


@runtime_checkable
class EventPublisher(Protocol):
    """Publish immutable notification events.

    An event records an observation, occurrence, or reason for consumers to reconsider
    state. Its payload is not an authoritative view of current state, and delivery may be
    delayed enough for that payload to become stale before a handler runs.

    Payload data may be used for routing, correlation, diagnostics, or as input to an
    authority-owned fence. State-dependent decisions and effects must obtain current state
    from the owning authority or use an authority operation that atomically revalidates the
    relevant expectation before committing the effect.
    """

    def publish(self, event: object) -> None: ...


@runtime_checkable
class EventSubscriber(Protocol):
    """Register ordered handlers for notification event payload types.

    Handlers must treat every delivered payload as potentially stale. A payload may identify
    the state, generation, or operation that caused the notification, but it does not prove
    that the same condition is still current when the handler executes.

    Before performing a state-dependent decision or effect, a handler must re-read current
    state from the owning authority or invoke an authority operation that atomically
    revalidates the relevant expectation.
    """

    def subscribe(
        self,
        event_type: type[EventT],
        handler: Callable[[EventT], None],
    ) -> EventSubscriptionToken: ...

    def unsubscribe(self, token: EventSubscriptionToken) -> bool: ...


class EventBus(EventPublisher, EventSubscriber, Protocol):
    """Combined notification publication and subscription contract.

    ``EventBus`` adds no state authority to event payloads. Consumers remain responsible for
    revalidating current state through the owning authority before state-dependent effects.
    """


__all__ = ["EventBus", "EventPublisher", "EventSubscriber"]
