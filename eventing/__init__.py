"""Infrastructure-neutral notification-event delivery contracts.

Events and signals are notifications or historical evidence, never authoritative views of
current state. Delivery may be delayed, so a payload may already be stale when a handler
runs. Consumers may use payload data for routing, correlation, diagnostics, or fencing, but
must re-read or atomically revalidate state through the owning authority before making a
state-dependent decision or effect.
"""

from eventing.models import (
    EventDispatchError,
    EventHandlerFailure,
    EventSubscriptionToken,
)
from eventing.ports import EventBus, EventPublisher, EventSubscriber

__all__ = [
    "EventBus",
    "EventDispatchError",
    "EventHandlerFailure",
    "EventPublisher",
    "EventSubscriber",
    "EventSubscriptionToken",
]
