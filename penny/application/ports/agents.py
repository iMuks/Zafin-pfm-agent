"""The conversational runtimes, seen from the delivery layer.

Both ports yield *components* — the validated UI objects defined in
`penny.application.components` — rather than tokens or markdown. That choice is
what lets the HTTP layer stay a dumb pipe and what lets a native mobile client
render charts and merchant cards instead of parsing prose.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class AgentRuntime(Protocol):
    """Answers one customer turn as a stream of UI components."""

    def stream(self, *, message: str, session_id: str) -> AsyncIterator[dict[str, Any]]: ...


@runtime_checkable
class GreetingRuntime(Protocol):
    """Produces the unprompted opener for a new session."""

    def stream(self) -> AsyncIterator[dict[str, Any]]: ...
