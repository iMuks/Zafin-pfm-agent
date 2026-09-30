"""The event bus port: publish a fact, react to facts.

In-process for fixtures and tests; a durable outbox over the ledger's
database in deployment (the worker drains it); a managed bus later if scale
demands it. Use cases and adapters only see this Protocol.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Protocol, runtime_checkable

from penny.domain.events import Event

Handler = Callable[[Event], None]


@runtime_checkable
class EventBus(Protocol):
    def publish(self, event: Event) -> None:
        """Record the fact and deliver it to every subscriber of its name."""
        ...

    def subscribe(self, event_type: type[Event], handler: Handler) -> None:
        """Deliver every future event of this type to `handler`."""
        ...
