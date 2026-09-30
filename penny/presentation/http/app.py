"""FastAPI application factory.

    uvicorn penny.presentation.http.app:app --reload

Endpoints:

    POST /agent/chat      streaming, chunked transfer, newline-delimited JSON
    GET  /agent/greeting  the unprompted opener, same component stream
    GET  /api/health      operational readiness + dataset summary (authenticated)
    GET  /healthz         liveness probe for the load balancer (never authenticated)
    GET  /                the phone-frame UI (static)
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from dotenv import load_dotenv

# Before any module reads the environment.
load_dotenv()

from fastapi import FastAPI  # noqa: E402
from fastapi.responses import JSONResponse  # noqa: E402
from fastapi.staticfiles import StaticFiles  # noqa: E402

from penny import __version__  # noqa: E402
from penny.application.ports.auth import TokenVerifier  # noqa: E402
from penny.domain.errors import PennyError  # noqa: E402
from penny.infrastructure.auth.cognito import build_verifier  # noqa: E402
from penny.infrastructure.config.settings import settings  # noqa: E402
from penny.presentation.http.middleware import RequestContextMiddleware  # noqa: E402
from penny.presentation.http.routes import router  # noqa: E402

WEB_DIR = Path(__file__).resolve().parents[3] / "web"


def create_app(
    web_dir: Path | None = None, *, verifier: TokenVerifier | None = None
) -> FastAPI:
    """Build the app. `verifier` overrides the configured one (tests inject a stub)."""
    app = FastAPI(
        title="Penny — Conversational PFM Agent",
        version=__version__,
        description=(
            "A chat-based spending companion for a US retail bank.\n\n"
            "Both agent endpoints stream **newline-delimited JSON**: one complete UI "
            "component per line, over HTTP chunked transfer. Read the body line by line "
            "and dispatch on each line's `component` key — do not buffer the whole "
            "response and parse it once.\n\n"
            "Conversation history lives server-side, keyed by session. Echo the "
            "`x-session-id` response header on your next request to continue a "
            "conversation.\n\n"
            "The full component contract, framing rules and client examples are in "
            "`docs/API.md`."
        ),
        openapi_tags=[
            {"name": "agent", "description": "Streaming conversational endpoints."},
            {"name": "operations", "description": "Readiness and dataset introspection."},
        ],
    )
    if verifier is None:
        verifier = build_verifier(settings())
    app.state.verifier = verifier
    app.add_middleware(RequestContextMiddleware, verifier=verifier)

    @app.exception_handler(PennyError)
    async def _penny_error(_request: Any, exc: PennyError) -> JSONResponse:
        # Every application error carries the status it maps to, so this handler
        # never has to enumerate error types.
        return JSONResponse(status_code=exc.status_code, content={"detail": str(exc)})

    app.include_router(router)

    # Mounted last: a catch-all at "/" would otherwise shadow the API routes.
    static_dir = web_dir or WEB_DIR
    if static_dir.exists():
        app.mount("/", StaticFiles(directory=static_dir, html=True), name="web")

    return app


app = create_app()
