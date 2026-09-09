"""Per-request context, carried in a ContextVar.

The alternative — threading a request object down through every call — would
push web concerns into the insight and tool layers, which are deliberately
framework-free. A ContextVar keeps them clean while still letting a log line
emitted five frames deep carry the request id that produced it.
"""

from __future__ import annotations

import time
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Any

from penny.infrastructure.observability.ids import new_id

_context: ContextVar[RequestContext | None] = ContextVar("penny_request_context", default=None)


@dataclass
class RequestContext:
    request_id: str
    poid: str  # pseudonymous customer id
    session_id: str | None = None
    model_id: str | None = None
    started_at: float = field(default_factory=time.perf_counter)
    tools_called: list[str] = field(default_factory=list)
    #: Request-scoped tool-result cache. A model that asks the same question
    #: twice in one turn pays for it once. Cheap here, where a tool is a list
    #: comprehension; essential upstream, where it is a network round trip.
    tool_cache: dict[str, Any] = field(default_factory=dict)

    def elapsed_ms(self) -> float:
        return (time.perf_counter() - self.started_at) * 1000


def set_context(ctx: RequestContext) -> None:
    _context.set(ctx)


def get_context() -> RequestContext | None:
    return _context.get()


def require_context() -> RequestContext:
    """The current context, creating an anonymous one if there is none.

    Background tasks and tests call into the stack without passing through HTTP
    middleware. Fabricating a context there is better than making every caller
    handle None, and the anonymous poid makes such calls obvious in the logs.
    """
    ctx = _context.get()
    if ctx is None:
        ctx = RequestContext(request_id=new_id("req"), poid="anonymous")
        _context.set(ctx)
    return ctx
