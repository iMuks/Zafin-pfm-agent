"""HTTP middleware: authentication, request context, access logging.

Two modes, chosen by `PENNY_AUTH_MODE`:

* `cognito` — the deployment. Every API request carries `Authorization: Bearer
  {JWT}` issued by the Cognito user pool; the middleware verifies it against the
  pool's key set and refuses the request with 401 otherwise. `/healthz` and the
  static client are exempt: the load balancer has no token, and the browser has
  to load the app before it can sign in.
* `none` — fixtures on a laptop. No verification; the request is attributed to a
  development identity. It reads the token's subject only to keep session
  scoping realistic, and it must never be enabled where real data lives.
"""

from __future__ import annotations

import base64
import binascii
import json

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.types import ASGIApp

from penny.application.ports.auth import TokenVerifier
from penny.domain.errors import InvalidTokenError, JwksUnavailableError, MissingTokenError
from penny.infrastructure.observability.context import RequestContext, new_id, set_context
from penny.infrastructure.observability.telemetry import log, setup

DEV_POID = "dev-customer"
_CLAIM_KEYS = ("poid", "sub", "customer_id", "id")

#: Paths that require a verified token in `cognito` mode. Everything else
#: (the static client, `/healthz`) is served without one.
PROTECTED_PREFIXES = (
    "/agent",
    "/api",
    "/chat",
    "/uploads",
    "/runs",
    "/snapshot",
    "/reviews",
    "/figures",
)


def _b64url_json(segment: str) -> dict | None:
    try:
        padded = segment + "=" * (-len(segment) % 4)
        decoded = json.loads(base64.urlsafe_b64decode(padded))
    except (ValueError, binascii.Error, UnicodeDecodeError):
        return None
    return decoded if isinstance(decoded, dict) else None


def poid_from_headers(authorization: str | None, eup: str | None) -> str:
    """Derive the development identity. NOT authentication (mode `none` only)."""
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


def bearer_token(authorization: str | None) -> str | None:
    if not authorization or not authorization.lower().startswith("bearer "):
        return None
    token = authorization.split(" ", 1)[1].strip()
    return token or None


def is_protected(path: str) -> bool:
    return path.startswith(PROTECTED_PREFIXES)


class RequestContextMiddleware(BaseHTTPMiddleware):
    def __init__(self, app: ASGIApp, verifier: TokenVerifier | None = None) -> None:
        super().__init__(app)
        self._verifier = verifier
        setup()

    def _identify(self, request: Request) -> str:
        """The pseudonymous customer id, or raise the auth error to return."""
        authorization = request.headers.get("authorization")
        if self._verifier is None:
            return poid_from_headers(
                authorization, request.headers.get("ext-user-profile-compressed")
            )
        token = bearer_token(authorization)
        if token is None:
            raise MissingTokenError
        return self._verifier.verify(token).poid

    async def dispatch(self, request: Request, call_next):
        request_id = request.headers.get("x-request-id") or new_id("req")
        path = request.url.path

        if self._verifier is not None and not is_protected(path):
            poid = DEV_POID  # static assets and the probe carry no identity
        else:
            try:
                poid = self._identify(request)
            except (MissingTokenError, InvalidTokenError) as exc:
                log("http.unauthenticated", path=path, reason=type(exc).__name__)
                return JSONResponse(
                    status_code=exc.status_code,
                    content={"detail": str(exc)},
                    headers={"x-request-id": request_id},
                )
            except JwksUnavailableError as exc:
                log("http.jwks_unavailable", path=path)
                return JSONResponse(
                    status_code=exc.status_code,
                    content={"detail": str(exc)},
                    headers={
                        "x-request-id": request_id,
                        "Retry-After": str(exc.retry_after_seconds),
                    },
                )

        ctx = RequestContext(
            request_id=request_id,
            poid=poid,
            session_id=request.headers.get("x-session-id"),
        )
        set_context(ctx)

        response = await call_next(request)
        response.headers["x-request-id"] = ctx.request_id
        if ctx.session_id:
            response.headers.setdefault("x-session-id", ctx.session_id)

        if path.startswith(("/agent", "/api")):
            # Note: for a streamed response this measures time to headers, not
            # to the last byte. End-to-end duration is in `stream.complete`.
            log(
                "http.request",
                method=request.method,
                path=path,
                status=response.status_code,
                duration_ms=round(ctx.elapsed_ms(), 1),
            )
        return response
