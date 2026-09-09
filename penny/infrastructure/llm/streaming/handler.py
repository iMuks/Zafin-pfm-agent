"""The chunk-processing engine: raw model text in, validated components out.

Sits between LangGraph's `astream_events` and the HTTP response, and is shared
by every caller that streams components — the chat agent and the greeting both
run through it, so the tolerant parsing, the contract validation and the
telemetry exist exactly once.

Responsibilities, in order:

1. accumulate model text deltas in a buffer
2. extract complete JSON objects (tolerantly)
3. discard anything that is not a component object
4. validate against the contract, degrading failures to `unsupported`
5. reject server-owned component names arriving from the model
6. yield each surviving component immediately
"""

from __future__ import annotations

import json
import time
from collections.abc import AsyncIterator
from typing import Any

from penny.application.components.contract import (
    MODEL_COMPONENTS,
    is_component_object,
    validate,
)
from penny.application.components.presenters import smart_loading, transaction_list
from penny.infrastructure.llm.streaming.jsonl import extract_json_objects
from penny.infrastructure.observability.telemetry import StreamTelemetry, log

#: What may reach the client from the model path: what the model is allowed to
#: emit, plus the placeholder a failed component degrades into.
_RENDERABLE = MODEL_COMPONENTS | {"unsupported"}


def text_of(chunk: Any) -> str:
    """Text from a message chunk, ignoring thinking blocks.

    With adaptive thinking enabled, `content` is a list of blocks rather than a
    string. Only text blocks belong in the JSONL buffer — appending a thinking
    block would corrupt it and lose the surrounding components.
    """
    content = getattr(chunk, "content", None)
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for block in content:
            if isinstance(block, str):
                parts.append(block)
            elif isinstance(block, dict) and block.get("type") in (None, "text"):
                text = block.get("text")
                if isinstance(text, str):
                    parts.append(text)
        return "".join(parts)
    return ""


def _tool_output(payload: Any) -> Any:
    """Normalise an `on_tool_end` payload down to the dict the tool returned."""
    output = payload.get("output") if isinstance(payload, dict) else None
    content = getattr(output, "content", output)
    if isinstance(content, str):
        try:
            return json.loads(content)
        except json.JSONDecodeError:
            return None
    return content


class StreamingResponseHandler:
    """Stateful buffer-to-component converter for one turn."""

    def __init__(self, telemetry: StreamTelemetry) -> None:
        self._buffer = ""
        self._telemetry = telemetry

    def _emit(self, objects: list[dict[str, Any]]) -> list[dict[str, Any]]:
        components: list[dict[str, Any]] = []
        for obj in objects:
            if not is_component_object(obj):
                self._telemetry.dropped_lines += 1
                continue

            component = validate(obj)
            name = component["component"]
            if name not in _RENDERABLE:
                # A server-owned name arriving from the model. Dropped rather
                # than rendered: the model must not be able to fabricate a
                # transaction-list, which the client trusts as verbatim data.
                log("stream.rejected_component", component=name)
                self._telemetry.dropped_lines += 1
                continue

            self._telemetry.component(name)
            components.append(component)
        return components

    def process(self, text: str) -> list[dict[str, Any]]:
        self._buffer += text
        objects, self._buffer = extract_json_objects(self._buffer)
        return self._emit(objects)

    def finalize(self) -> list[dict[str, Any]]:
        """Flush whatever survives at end of stream; drop an incomplete tail."""
        leftover, self._buffer = self._buffer, ""
        if not leftover.strip():
            return []
        objects, remainder = extract_json_objects(leftover)
        if remainder.strip():
            self._telemetry.dropped_lines += 1
            log("stream.truncated_tail", bytes=len(remainder))
        return self._emit(objects)


async def components_from_events(
    events: AsyncIterator[dict[str, Any]],
    telemetry: StreamTelemetry,
) -> AsyncIterator[dict[str, Any]]:
    """Turn a LangChain/LangGraph event stream into UI components.

    Tool calls stay internal to the server. The client sees a `smart-loading`
    label while one runs, and a `transaction-list` when one produces rows — it
    never learns the tool protocol, which is what keeps the wire contract stable
    while tools change behind it.
    """
    handler = StreamingResponseHandler(telemetry)
    started: dict[str, float] = {}

    async for event in events:
        kind = event.get("event")

        if kind == "on_chat_model_stream":
            text = text_of(event["data"].get("chunk"))
            if text:
                telemetry.first_token()
                for component in handler.process(text):
                    yield component

        elif kind == "on_tool_start":
            name = event.get("name", "tool")
            started[event.get("run_id", name)] = time.perf_counter()
            yield smart_loading(name)

        elif kind == "on_tool_end":
            name = event.get("name", "tool")
            begun = started.pop(event.get("run_id", name), None)
            if begun is not None:
                telemetry.tool(name, (time.perf_counter() - begun) * 1000)
            cards = transaction_list(name, _tool_output(event.get("data")))
            if cards:
                telemetry.component("transaction-list")
                yield cards

    for component in handler.finalize():
        yield component
