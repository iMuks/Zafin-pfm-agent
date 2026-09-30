"""The chat agent: START -> initializer -> react_agent -> persist -> END.

The ReAct loop itself is LangChain's `create_agent`, mounted as a single node.
The nodes on either side are what make a turn stateful and auditable:

* **initializer** — loads conversation history for the session and stamps the
  resolved model identity onto the state. Nothing else in the graph touches the
  session store, so memory has exactly one entry point.
* **react_agent** — the tool-calling loop, bounded by `ModelCallLimitMiddleware`
  and gated by `ToolCallValidator`.
* **persist** — writes both turns and schedules the audit record. It runs after
  the response has finished streaming, so persistence never delays the answer.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Annotated, Any, TypedDict

from langchain.agents import create_agent
from langchain.agents.middleware import ModelCallLimitMiddleware
from langchain_core.messages import AIMessage, AnyMessage, HumanMessage, SystemMessage
from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import add_messages

from penny.application.components.contract import is_component_object
from penny.application.components.presenters import done, notice, try_again_error
from penny.application.conversation.prompts.assembler import PromptAssembler
from penny.application.ports.agents import AgentRuntime
from penny.application.ports.audit import AuditRecord, AuditSink
from penny.application.ports.sessions import SessionRepository
from penny.application.tools.registry import ToolRegistry
from penny.infrastructure.config.settings import settings
from penny.infrastructure.llm.middleware import ToolCallValidator
from penny.infrastructure.llm.model_factory import chat_model
from penny.infrastructure.llm.streaming.handler import (
    components_from_events,
    guardrail_assessment,
    text_of,
)
from penny.infrastructure.llm.streaming.jsonl import extract_json_objects
from penny.infrastructure.llm.tool_adapter import build_tools
from penny.infrastructure.observability.context import require_context
from penny.infrastructure.observability.telemetry import StreamTelemetry, log, span


class ChatState(TypedDict, total=False):
    messages: Annotated[list[AnyMessage], add_messages]
    poid: str
    session_id: str
    model_id: str
    query: str


def answer_text(messages: list[AnyMessage]) -> str:
    """Recover a plain-text answer from the streamed JSONL, for history and audit.

    History is stored as prose rather than as raw components on purpose:
    replaying JSONL into the next turn teaches the model to quote its own wire
    format back, and the format prompt then has to fight its own examples.
    """
    for message in reversed(messages):
        if not isinstance(message, AIMessage):
            continue
        # With adaptive thinking on, live content is a list of blocks rather
        # than a string. A str-only read silently yields "" and the assistant
        # turn is persisted empty, so the next turn has no memory of it.
        raw = text_of(message)
        objects, _ = extract_json_objects(raw)
        bodies = [
            obj["body"]
            for obj in objects
            if is_component_object(obj) and obj["component"] == "chat-response" and obj.get("body")
        ]
        if bodies:
            return "\n".join(bodies)
        if raw.strip():
            # The model answered in prose rather than components. Keep it —
            # losing the turn entirely is worse than storing an off-format one.
            return raw.strip()
    return ""


def _guardrail_stopped(messages: list[AnyMessage]) -> bool:
    """True when the last model message carries Bedrock's intervention stop reason."""
    for message in reversed(messages):
        if isinstance(message, AIMessage):
            return guardrail_assessment(message) is not None
    return False


class PennyChatRuntime(AgentRuntime):
    """`AgentRuntime` over a compiled LangGraph workflow."""

    def __init__(
        self,
        *,
        registry: ToolRegistry,
        prompts: PromptAssembler,
        sessions: SessionRepository,
        audit: AuditSink,
        model: Any | None = None,
    ) -> None:
        self._registry = registry
        self._prompts = prompts
        self._sessions = sessions
        self._audit = audit
        self._graph = self._build(model)

    # -- graph ------------------------------------------------------------

    def _build(self, model: Any | None) -> Any:
        cfg = settings()
        agent = create_agent(
            model or chat_model("chat"),
            build_tools(self._registry),
            system_prompt=SystemMessage(content=self._prompts.blocks()),
            middleware=[
                # Bounds the ReAct loop. On the limit the agent ends its turn
                # rather than raising, so the customer still receives whatever
                # it has managed to work out.
                ModelCallLimitMiddleware(run_limit=cfg.max_model_calls, exit_behavior="end"),
                ToolCallValidator(self._registry),
            ],
        )

        graph = StateGraph(ChatState)
        graph.add_node("initializer", self._initializer)
        graph.add_node("react_agent", agent)
        graph.add_node("persist", self._persist)
        graph.add_edge(START, "initializer")
        graph.add_edge("initializer", "react_agent")
        graph.add_edge("react_agent", "persist")
        graph.add_edge("persist", END)
        return graph.compile()

    async def _initializer(self, state: ChatState) -> dict[str, Any]:
        ctx = require_context()
        history = self._sessions.history(state["session_id"], limit=settings().max_history_turns)
        log("graph.initializer", history_turns=len(history))

        prior: list[AnyMessage] = [
            AIMessage(content=turn.content)
            if turn.role == "assistant"
            else HumanMessage(content=turn.content)
            for turn in history
        ]
        # History is prepended; `add_messages` appends, so the new question
        # remains the last message the model sees.
        return {
            "messages": prior + [HumanMessage(content=state["query"])],
            "model_id": ctx.model_id or "",
        }

    async def _persist(self, state: ChatState) -> dict[str, Any]:
        ctx = require_context()
        session_id = state["session_id"]
        messages = list(state.get("messages", []))
        if _guardrail_stopped(messages):
            # Neither turn is kept: the canned text Bedrock substituted is not
            # Penny's answer, and replaying the blocked question would put it
            # in front of the model again. `stream` records the event itself.
            log("graph.persist_skipped", reason="guardrail_intervened")
            return {}
        answer = answer_text(messages)

        self._sessions.append_turn(session_id, "user", state["query"])
        if answer:
            self._sessions.append_turn(session_id, "assistant", answer)

        self._audit.record(
            AuditRecord(
                request_id=ctx.request_id,
                session_id=session_id,
                poid=ctx.poid,
                client_query=state["query"],
                ai_response=answer,
                tools_called=tuple(ctx.tools_called),
                model_id=state.get("model_id") or ctx.model_id,
                prompt_version=self._prompts.version,
            )
        )
        log("graph.persisted", answer_chars=len(answer))
        return {}

    # -- AgentRuntime -----------------------------------------------------

    async def stream(self, *, message: str, session_id: str) -> AsyncIterator[dict[str, Any]]:
        ctx = require_context()
        telemetry = StreamTelemetry(model_id=ctx.model_id)
        state: ChatState = {
            "poid": ctx.poid,
            "session_id": session_id,
            "query": message,
            "messages": [],
        }

        with span("agent.turn", model_id=ctx.model_id):
            events = self._graph.astream_events(state, version="v2")
            async for component in components_from_events(events, telemetry):
                yield component

            if telemetry.guardrail_intervened:
                # Guardrails stopped the prompt or the response. The customer
                # sees one fixed line; the audit trail records that it happened
                # and the Bedrock request id, never the content (REV-3).
                self._audit.record(
                    AuditRecord(
                        request_id=ctx.request_id,
                        session_id=session_id,
                        poid=ctx.poid,
                        client_query="",
                        ai_response="",
                        tools_called=tuple(ctx.tools_called),
                        model_id=ctx.model_id,
                        prompt_version=self._prompts.version,
                        kind="guardrail_blocked",
                        reference=telemetry.guardrail_assessment,
                    )
                )
                telemetry.emit("guardrail_blocked")
                yield notice()
                yield done(telemetry.summary())
                return

            if telemetry.empty_response:
                # The turn produced nothing renderable. Say so, rather than
                # leaving the customer looking at an empty bubble.
                telemetry.emit("empty")
                yield try_again_error(
                    "I couldn't put an answer together for that one. Try rephrasing it?"
                )
                return

        yield {"component": "feedback"}
        telemetry.emit("ok")
        yield done(telemetry.summary())
