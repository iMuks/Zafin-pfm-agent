"""The opener runtime.

Runs a live model call on every session open — never a cached greeting — because
the assignment requires the agent to call a real LLM API rather than serve a
static mock. Effort is `low` and the model is the cheapest in the registry:
this turn has no tools to choose between and no arithmetic to do, only phrasing,
and first paint is what the customer is waiting on.

If the model call fails, or no API key is configured, a deterministic template
over the *same* pre-computed facts is emitted instead. That is a documented
fallback, not a mock: whenever a key is present the live path is the only path.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from typing import Any

from langchain_core.messages import HumanMessage, SystemMessage

from penny.application.components.contract import validate
from penny.application.components.presenters import done
from penny.application.conversation.greeting import GREETING_BEHAVIORAL, GreetingFacts
from penny.application.conversation.prompts.assembler import PromptAssembler
from penny.application.ports.agents import GreetingRuntime
from penny.infrastructure.llm.model_factory import chat_model
from penny.infrastructure.llm.streaming.handler import components_from_events
from penny.infrastructure.observability.context import require_context
from penny.infrastructure.observability.telemetry import StreamTelemetry, log, span


class PennyGreetingRuntime(GreetingRuntime):
    def __init__(self, *, facts: GreetingFacts, prompts: PromptAssembler) -> None:
        self._facts = facts
        self._prompts = prompts

    async def stream(self) -> AsyncIterator[dict[str, Any]]:
        ctx = require_context()
        facts = self._facts.build()
        telemetry = StreamTelemetry(model_id=ctx.model_id)

        system = SystemMessage(content=self._prompts.blocks(behavioral=GREETING_BEHAVIORAL))
        human = HumanMessage(
            content="Facts you may use (do not compute anything else):\n"
            + json.dumps(facts, indent=2, default=str)
        )

        produced = False
        try:
            with span("greeting.turn", model_id=ctx.model_id):
                events = chat_model("greeting").astream_events([system, human], version="v2")
                async for component in components_from_events(events, telemetry):
                    produced = True
                    yield component
        except Exception as exc:
            log("greeting.failed", error=str(exc))

        if not produced:
            log("greeting.fallback")
            for component in self._facts.fallback_components(facts):
                telemetry.component(component["component"])
                yield validate(component)

        telemetry.emit("ok" if produced else "fallback")
        yield done()
