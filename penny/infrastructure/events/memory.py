"""In-process event bus: synchronous, ordered, handler failures isolated.

Used on the laptop and in tests. A handler that raises is logged and does not
stop the others; the event is never lost because the bus keeps the log of
everything published, which is also what tests assert against.
"""

from __future__ import annotations

from collections import defaultdict

from penny.application.ports.events import EventBus, Handler
from penny.domain.events import Event
from penny.infrastructure.observability.telemetry import log


class InMemoryEventBus(EventBus):
    def __init__(self) -> None:
        self._handlers: dict[type[Event], list[Handler]] = defaultdict(list)
        self.published: list[Event] = []
        self._seen: set[str] = set()

    def subscribe(self, event_type: type[Event], handler: Handler) -> None:
        self._handlers[event_type].append(handler)

    def publish(self, event: Event) -> None:
        if event.event_id in self._seen:
            return  # at-most-once delivery for a re-published fact
        self._seen.add(event.event_id)
        self.published.append(event)
        for handler in list(self._handlers.get(type(event), ())):
            try:
                handler(event)
            except Exception as exc:  # one bad handler must not starve the rest
                log(
                    "event.handler_failed",
                    event_name=event.name,
                    handler=getattr(handler, "__name__", "?"),
                    error=str(exc),
                )

    def of(self, event_type: type[Event]) -> list[Event]:
        return [e for e in self.published if isinstance(e, event_type)]
