"""HTTP middleware: identity extraction, request context, access logging.

The production contract carries two tokens — `Authorization: Bearer {JWT}` and
`ext-user-profile-compressed: {EUP}` — and the gateway validates both before
extracting a pseudonymous customer id (poid) used for session scoping and audit.

**This prototype does not verify either token and must not be read as if it
does.** There is no identity provider to verify against. What it does is mirror
the *shape*: read the headers when present, derive a poid, scope every session
and audit record to it, and fall back to a development identity when absent.
The seam is real and the poid flows through the entire request; the cryptography
is the part a real deployment must add, and it is called out in the build report
rather than glossed over.
"""

from __future__ import annotations

import base64
import binascii
import json

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.types import ASGIApp

from penny.infrastructure.observability.context import RequestContext, new_id, set_context
from penny.infrastructure.observability.telemetry import log, setup

DEV_POID = "dev-customer"
_CLAIM_KEYS = ("poid", "sub", "customer_id", "id")


def _b64url_json(segment: str) -> dict | None:
    try:
        padded = segment + "=" * (-len(segment) % 4)
        decoded = json.loads(base64.urlsafe_b64decode(padded))
    except (ValueError, binascii.Error, UnicodeDecodeError):
        return None
    return decoded if isinstance(decoded, dict) else None


def poid_from_headers(authorization: str | None, eup: str | None) -> str:
    """Derive the customer id. NOT authentication — see the module docstring."""
    if eup:
        claims = _b64url_json(eup)
        if claims:
            for key in _CLAIM_KEYS:
                if isinstance(claims.get(key), str):
                    return claims[key]

    if authorization and authorization.lower().startswith("bearer "):
        parts = authorization.split(" ", 1)[1].split(".")
        if len(parts) == 3:  # an unverified JWT payload, read for its subject only
            claims = _b64url_json(parts[1])
            if claims:
                for key in ("poid", "sub"):
                    if isinstance(claims.get(key), str):
                        return claims[key]

    return DEV_POID


class RequestContextMiddleware(BaseHTTPMiddleware):
    def __init__(self, app: ASGIApp) -> None:
        super().__init__(app)
        setup()

    async def dispatch(self, request: Request, call_next):
        ctx = RequestContext(
            request_id=request.headers.get("x-request-id") or new_id("req"),
            poid=poid_from_headers(
                request.headers.get("authorization"),
                request.headers.get("ext-user-profile-compressed"),
            ),
            session_id=request.headers.get("x-session-id"),
        )
        set_context(ctx)

        response = await call_next(request)
        response.headers["x-request-id"] = ctx.request_id
        if ctx.session_id:
            response.headers.setdefault("x-session-id", ctx.session_id)

        if request.url.path.startswith(("/agent", "/api")):
            # Note: for a streamed response this measures time to headers, not
            # to the last byte. End-to-end duration is in `stream.complete`.
            log(
                "http.request",
                method=request.method,
                path=request.url.path,
                status=response.status_code,
                duration_ms=round(ctx.elapsed_ms(), 1),
            )
        return response
