"""Phase 1 — run the question set against the live agent.

Calls the compiled graph directly rather than over HTTP: the target under test
is the agent, and skipping the transport keeps the run fast and removes a source
of flakiness that has nothing to do with answer quality.

Each case gets its own session, so one question's history can never leak into
another's answer. Concurrency is bounded — the point is to exercise the agent,
not to rate-limit ourselves.
"""

from __future__ import annotations

import asyncio

from eval.dataset import EvalCase
from eval.models import Simulation
from penny.application.components.contract import is_component_object
from penny.application.ports.agents import AgentRuntime
from penny.application.ports.sessions import SessionRepository
from penny.composition.container import container
from penny.infrastructure.config.models import resolve_model
from penny.infrastructure.observability.context import RequestContext, new_id, set_context

DEFAULT_CONCURRENCY = 3


async def _run_one(
    runtime: AgentRuntime,
    sessions: SessionRepository,
    case: EvalCase,
) -> Simulation:
    ctx = RequestContext(
        request_id=new_id("eval"),
        poid=f"eval-{case.case_id}",
        model_id=resolve_model("chat").model_id,
    )
    # ContextVar is per-task, so cases running in parallel stay isolated.
    set_context(ctx)
    session = sessions.get_or_create(ctx.poid, None)
    ctx.session_id = session.session_id

    bodies: list[str] = []
    components: list[str] = []
    telemetry: dict = {}
    error: str | None = None

    try:
        async for component in runtime.stream(message=case.question, session_id=session.session_id):
            if not is_component_object(component):
                continue
            name = component["component"]
            if name == "done":
                telemetry = component.get("telemetry") or {}
                continue
            components.append(name)
            if name == "chat-response":
                bodies.append(component.get("body", ""))
    except Exception as exc:
        # A case that blows up is recorded, not raised: one bad question must not
        # discard the other thirteen answers already paid for.
        error = f"{type(exc).__name__}: {exc}"

    return Simulation(
        case_id=case.case_id,
        question=case.question,
        answer="\n".join(bodies).strip(),
        components=components,
        ttfc_ms=telemetry.get("ttfc_ms"),
        tool_calls=telemetry.get("tool_calls", 0),
        error=error,
    )


async def simulate(
    cases: list[EvalCase], concurrency: int = DEFAULT_CONCURRENCY
) -> list[Simulation]:
    """Run every case, at most `concurrency` at a time."""
    runtime = container.chat_runtime()
    sessions = container.sessions()
    gate = asyncio.Semaphore(concurrency)

    async def guarded(case: EvalCase) -> Simulation:
        async with gate:
            return await _run_one(runtime, sessions, case)

    return list(await asyncio.gather(*(guarded(case) for case in cases)))
