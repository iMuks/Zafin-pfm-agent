"""Binds the application's `ToolRegistry` to LangChain.

`penny.application.tools` holds the single definition of every tool. This module
*adapts* those definitions rather than restating them, so a schema change has
exactly one edit site and the LangChain dependency stays out of the application
layer.

Each adapter is a coroutine, and the insight call runs in a worker thread. The
tool surface is therefore genuinely async: several tools in one turn execute
concurrently and the event loop is never blocked. Here the work is a list
comprehension and the thread hop is nearly pointless — but upstream these are
database round trips, and the async shape is what generalises.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Callable
from typing import Any

from langchain_core.tools import StructuredTool

from penny.application.tools.registry import ToolRegistry
from penny.infrastructure.observability.context import require_context


def _cache_key(name: str, payload: dict[str, Any]) -> str:
    return f"{name}:{json.dumps(payload, sort_keys=True, default=str)}"


def _make_coroutine(registry: ToolRegistry, name: str) -> Callable[..., Any]:
    async def _call(**payload: Any) -> dict[str, Any]:
        ctx = require_context()
        key = _cache_key(name, payload)
        if key in ctx.tool_cache:
            return ctx.tool_cache[key]

        result = await asyncio.to_thread(registry.run, name, payload)
        ctx.tool_cache[key] = result
        ctx.tools_called.append(name)
        return result

    _call.__name__ = name
    return _call


def build_tools(registry: ToolRegistry) -> list[StructuredTool]:
    """One `StructuredTool` per registered tool, in declared order.

    Order is stable and that is load-bearing: a tool list whose order varies
    between requests changes the serialised prefix and silently destroys prompt
    caching, which shows up as a latency and cost regression nobody can explain.
    """
    return [
        StructuredTool.from_function(
            coroutine=_make_coroutine(registry, spec.name),
            name=spec.name,
            description=spec.description,
            args_schema=dict(spec.input_schema),
        )
        for spec in registry
    ]
