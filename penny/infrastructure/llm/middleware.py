"""Agent middleware.

`ModelCallLimitMiddleware` ships with LangChain and covers the runaway-loop
case. `ToolCallValidator` below is ours: it refuses a call whose name is unknown
or whose arguments do not satisfy the declared schema, and returns the refusal
to the model as a tool result rather than raising.

That distinction is the entire point. A raise ends the turn and shows the
customer an error. A returned error lets the model read "that tool does not
exist, here are the ones that do" and recover inside the same conversation,
which it reliably does.
"""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable
from typing import Any

from langchain.agents.middleware import AgentMiddleware
from langchain_core.messages import ToolMessage

from penny.application.tools.registry import ToolRegistry
from penny.infrastructure.observability.telemetry import log


class ToolCallValidator(AgentMiddleware):
    """Schema-checks every tool call before it is allowed to execute."""

    name = "ToolCallValidator"

    def __init__(self, registry: ToolRegistry) -> None:
        super().__init__()
        self._registry = registry

    async def awrap_tool_call(
        self,
        request: Any,
        handler: Callable[[Any], Awaitable[Any]],
    ) -> Any:
        call = getattr(request, "tool_call", None) or {}
        name = call.get("name", "")
        args = call.get("args", {}) or {}

        reason = self._registry.validate(name, args)
        if reason is None:
            return await handler(request)

        log("tool.rejected", tool=name, reason=reason)
        return ToolMessage(
            content=json.dumps({"error": reason}),
            tool_call_id=call.get("id", "unknown"),
            name=name or "unknown_tool",
            status="error",
        )
