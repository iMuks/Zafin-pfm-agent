"""Route handlers.

Handlers own no logic. Each one resolves a collaborator from the container,
adapts the HTTP envelope, and streams components. Everything else lives behind a
port — which is why these functions are short and why none of them mentions
Claude, LangGraph or JSON files.
"""

from __future__ import annotations

import json
import os
from collections.abc import AsyncIterator
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import TypeAdapter

from penny.application.components.presenters import try_again_error
from penny.composition.container import container
from penny.domain.errors import DataNotEnrichedError
from penny.infrastructure.config.models import resolve_model
from penny.infrastructure.config.settings import settings
from penny.infrastructure.observability.context import require_context
from penny.infrastructure.observability.telemetry import log
from penny.presentation.http.schemas import (
    ChatRequest,
    ComponentLine,
    HealthResponse,
)

router = APIRouter()

#: Chunked transfer with buffering disabled. Without `X-Accel-Buffering: no` an
#: nginx in front of this would hold the whole stream and deliver it at once,
#: which silently destroys the entire streaming experience.
JSONL_HEADERS = {"Cache-Control": "no-cache", "X-Accel-Buffering": "no"}

#: OpenAPI cannot express newline-delimited JSON as a body schema, and the
#: default `application/json` would be a lie that breaks any generated client:
#: it would parse the whole body once instead of reading it line by line. So the
#: response is declared as text/plain with an example that shows the framing.
_JSONL_EXAMPLE = (
    '{"component":"smart-loading","body":"Adding up category spend"}\n'
    '{"component":"chat-response","body":"You spent $412.80 on Dining in June."}\n'
    '{"component":"bar-chart","valueTitle":"Spend ($)","labels":["Dining"],'
    '"data":[{"label":"May","values":[349.1]},{"label":"Jun","values":[412.8]}]}\n'
    '{"component":"suggested-user-intents","intents":["Compare to May"]}\n'
    '{"component":"feedback"}\n'
    '{"component":"done","telemetry":{"ttft_ms":2463.0,"ttfc_ms":3911.5,'
    '"components":3,"tool_calls":1}}\n'
)

_JSONL_RESPONSE: dict[int | str, dict[str, Any]] = {
    200: {
        "description": (
            "Newline-delimited JSON over chunked transfer. Each line is one complete UI "
            "component identified by its `component` key; read the body line by line and "
            "render each line as it arrives. `done` terminates the stream. Consumers must "
            "tolerate an unknown `component` value by ignoring or placeholder-rendering it, "
            "so a newer server can add components without breaking older clients."
        ),
        # The body is NDJSON, so the media type is text/plain — but each *line*
        # conforms to ComponentLine, and publishing that schema is what lets a
        # consumer generate a typed client instead of guessing.
        "content": {
            "text/plain": {
                "schema": {
                    "title": "NewlineDelimitedComponentLines",
                    "type": "string",
                    "description": "One JSON object per line; each line validates against ComponentLine.",
                    # Self-contained: nested models resolve to local `$defs`,
                    # so the line schema carries no refs into the parent
                    # document and can be lifted out and used on its own.
                    "x-ndjson-line-schema": TypeAdapter(ComponentLine).json_schema(),
                },
                "example": _JSONL_EXAMPLE,
            }
        },
        "headers": {
            "x-session-id": {
                "description": "Server-side conversation id. Echo it back on the next "
                "request to continue the same conversation.",
                "schema": {"type": "string"},
            },
            "x-request-id": {
                "description": "Correlation id for this request, also present in server logs.",
                "schema": {"type": "string"},
            },
        },
    },
    503: {"description": "Dataset not enriched yet, or no model credentials configured."},
}


def _jwks_status(request: Request) -> str:
    """`ok`, `unavailable`, or `n/a` when no verifier is configured."""
    verifier = getattr(request.app.state, "verifier", None)
    return verifier.status if verifier is not None else "n/a"


@router.get(
    "/healthz",
    summary="Liveness probe",
    tags=["operations"],
    include_in_schema=False,
)
def healthz(request: Request) -> dict[str, str]:
    """What the load balancer asks, and nothing else.

    Exempt from authentication and deliberately empty of everything the full
    health page reports: no model, no tools, no dataset. It answers one
    question, is the process alive and can it verify a sign-in.
    """
    return {"status": "ok", "jwks": _jwks_status(request)}


def _coverage() -> dict[str, Any]:
    """Dataset summary, or a 503 explaining exactly how to fix it."""
    try:
        return container.insights().get_data_coverage()
    except DataNotEnrichedError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


async def _jsonl(components: AsyncIterator[dict[str, Any]]) -> AsyncIterator[str]:
    """One complete component per line, flushed as soon as it is ready.

    A failure mid-stream becomes a renderable component rather than an
    exception: the response headers have already been sent, so raising here
    would truncate the body and leave the customer with a half-drawn answer and
    no explanation.
    """
    try:
        async for component in components:
            yield json.dumps(component, default=str) + "\n"
    except Exception as exc:
        log("stream.failed", error=str(exc))
        yield json.dumps(try_again_error("Something went wrong on my side. Try that again?")) + "\n"


@router.get(
    "/api/health",
    summary="Readiness and dataset summary",
    tags=["operations"],
    response_model=HealthResponse,
    responses={503: {"description": "The enriched dataset is missing."}},
)
def health(request: Request) -> dict[str, Any]:
    """Operational readiness — and the dataset summary the UI chrome displays."""
    entry = resolve_model("chat")
    cfg = settings()
    endpoint = {
        "local": cfg.local_base_url,
        "bedrock": cfg.bedrock_endpoint_url or None,
    }.get(entry.provider)
    return {
        "status": "ok",
        "model": entry.model_id,
        "provider": entry.provider,
        "endpoint": endpoint,
        "auth_mode": cfg.auth_mode,
        "jwks": _jwks_status(request),
        "effort": entry.effort,
        "prompt_version": cfg.prompt_version,
        "api_key_configured": bool(os.getenv("ANTHROPIC_API_KEY")),
        "greeting_enabled": cfg.enable_greeting,
        "tools": container.tool_registry().names,
        "data": _coverage(),
    }


#: The brief suggests serving a `/chat` endpoint; the production wire contract
#: this mirrors uses `/agent/chat`. Both are served by the same handler so
#: neither reading is wrong. The alias is hidden from the schema so the
#: generated docs advertise one canonical path.
@router.post(
    "/chat",
    summary="Ask Penny a question (streaming) - alias of /agent/chat",
    tags=["agent"],
    response_class=StreamingResponse,
    responses=_JSONL_RESPONSE,
    include_in_schema=False,
)
@router.post(
    "/agent/chat",
    summary="Ask Penny a question (streaming)",
    description=(
        "Send one customer message and receive a stream of UI components.\n\n"
        "Conversation history is **not** part of the request: it is held server-side "
        "and keyed by session, so a client cannot replay or rewrite earlier turns. "
        "Pass the `x-session-id` header (or `session_id` in the body) returned by a "
        "previous call to continue a conversation; omit it to start a new one."
    ),
    tags=["agent"],
    response_class=StreamingResponse,
    responses=_JSONL_RESPONSE,
)
async def chat(request: ChatRequest) -> StreamingResponse:
    _coverage()  # fail fast and legibly rather than mid-stream

    ctx = require_context()
    ctx.model_id = resolve_model("chat").model_id
    session = container.sessions().get_or_create(ctx.poid, request.session_id or ctx.session_id)
    ctx.session_id = session.session_id

    stream = container.chat_runtime().stream(message=request.message, session_id=session.session_id)
    return StreamingResponse(
        _jsonl(stream),
        media_type="text/plain",
        headers={**JSONL_HEADERS, "x-session-id": session.session_id},
    )


@router.get(
    "/agent/greeting",
    summary="Penny's unprompted opener (streaming)",
    description=(
        "The proactive summary Penny offers when a session opens: a real figure from "
        "the customer's recent spending plus follow-up intents. Same component stream "
        "as `/agent/chat`. A live model call on every request — never cached."
    ),
    tags=["agent"],
    response_class=StreamingResponse,
    responses={
        **_JSONL_RESPONSE,
        404: {"description": "The greeting is disabled by configuration."},
    },
)
async def greeting() -> StreamingResponse:
    """Penny's opener. A live model call on every session open — never cached."""
    _coverage()
    if not settings().enable_greeting:
        raise HTTPException(status_code=404, detail="Greeting is disabled.")

    ctx = require_context()
    ctx.model_id = resolve_model("greeting").model_id
    session = container.sessions().get_or_create(ctx.poid, ctx.session_id)
    ctx.session_id = session.session_id

    return StreamingResponse(
        _jsonl(container.greeting_runtime().stream()),
        media_type="text/plain",
        headers={**JSONL_HEADERS, "x-session-id": session.session_id},
    )
