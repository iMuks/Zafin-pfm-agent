"""Cognito JWT verification against the user pool's JWKS.

The contract (design revision 3, REV-2):

* the key set is fetched at startup and cached;
* a token signed with an unknown `kid` triggers one refresh, at most one per
  `refresh_min_seconds`, then fails with 401 if the key is still unknown;
* if the cache is empty and the fetch fails, the request is answered 503 with
  Retry-After, never 401, and the health probe reports `jwks: unavailable`.

A 401 therefore always means a bad token, and a rotation of the pool's keys
never locks the customer out.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from typing import Any

import jwt
from jwt import PyJWK

from penny.application.ports.auth import Identity
from penny.domain.errors import InvalidTokenError, JwksUnavailableError
from penny.infrastructure.observability.telemetry import log

JwksFetcher = Callable[[], dict[str, Any]]


def cognito_issuer(region: str, user_pool_id: str) -> str:
    return f"https://cognito-idp.{region}.amazonaws.com/{user_pool_id}"


def http_jwks_fetcher(jwks_url: str, timeout_seconds: float = 5.0) -> JwksFetcher:
    """The production fetcher: one GET of the well-known key set."""
    import httpx

    def fetch() -> dict[str, Any]:
        response = httpx.get(jwks_url, timeout=timeout_seconds)
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, dict) or "keys" not in payload:
            raise ValueError("JWKS payload has no keys")
        return payload

    return fetch


class CognitoTokenVerifier:
    """Verifies Cognito access and id tokens; see the module docstring."""

    status: str

    def __init__(
        self,
        *,
        issuer: str,
        app_client_id: str,
        fetch_jwks: JwksFetcher,
        refresh_min_seconds: float = 60.0,
        clock: Callable[[], float] = time.monotonic,
        retry_after_seconds: int = 30,
    ) -> None:
        self._issuer = issuer
        self._client_id = app_client_id
        self._fetch = fetch_jwks
        self._refresh_min = refresh_min_seconds
        self._clock = clock
        self._retry_after = retry_after_seconds
        self._keys: dict[str, PyJWK] = {}
        self._last_refresh: float | None = None
        self.status = "unavailable"
        self._refresh(initial=True)

    # -- key set ------------------------------------------------------------

    def _refresh(self, *, initial: bool = False) -> bool:
        """Fetch the key set. Returns True on success; never raises."""
        self._last_refresh = self._clock()
        try:
            payload = self._fetch()
            keys = {
                key["kid"]: PyJWK.from_dict(key)
                for key in payload.get("keys", [])
                if isinstance(key, dict) and key.get("kid")
            }
        except Exception as exc:  # network, JSON, malformed key: all the same to the caller
            log("jwks.fetch_failed", error=type(exc).__name__, initial=initial)
            self.status = "ok" if self._keys else "unavailable"
            return False
        if keys:
            self._keys = keys
        self.status = "ok" if self._keys else "unavailable"
        return True

    def _refresh_allowed(self) -> bool:
        return (
            self._last_refresh is None or (self._clock() - self._last_refresh) >= self._refresh_min
        )

    def _key_for(self, kid: str | None) -> PyJWK:
        if not self._keys:
            # Nothing cached: try once more now, then say so honestly.
            if self._refresh_allowed():
                self._refresh()
            if not self._keys:
                raise JwksUnavailableError(self._retry_after)
        if kid in self._keys:
            return self._keys[kid]
        # Unknown kid: the pool may have rotated its keys. One refresh, rate-limited.
        if self._refresh_allowed():
            self._refresh()
            if kid in self._keys:
                return self._keys[kid]
        log("jwt.unknown_kid", kid=kid)
        raise InvalidTokenError

    # -- verification ---------------------------------------------------------

    def verify(self, token: str) -> Identity:
        try:
            header = jwt.get_unverified_header(token)
        except jwt.PyJWTError as exc:
            log("jwt.rejected", reason=type(exc).__name__)
            raise InvalidTokenError from exc

        key = self._key_for(header.get("kid"))
        try:
            claims = jwt.decode(
                token,
                key=key.key,
                algorithms=["RS256"],
                issuer=self._issuer,
                # Audience differs by token type and is checked below.
                options={"verify_aud": False, "require": ["exp", "iss", "sub"]},
            )
        except jwt.PyJWTError as exc:
            log("jwt.rejected", reason=type(exc).__name__, kid=header.get("kid"))
            raise InvalidTokenError from exc

        token_use = claims.get("token_use")
        if token_use == "access":
            audience_ok = claims.get("client_id") == self._client_id
        elif token_use == "id":
            audience_ok = claims.get("aud") == self._client_id
        else:
            audience_ok = False
        if not audience_ok:
            log("jwt.rejected", reason="audience", token_use=token_use)
            raise InvalidTokenError

        subject = str(claims["sub"])
        return Identity(poid=subject, subject=subject, token_use=str(token_use))


def build_verifier(cfg: Any) -> CognitoTokenVerifier | None:
    """The verifier for the configured auth mode, or None for mode `none`."""
    if cfg.auth_mode != "cognito":
        return None
    if not cfg.cognito_user_pool_id or not cfg.cognito_app_client_id:
        raise ValueError(
            "PENNY_AUTH_MODE=cognito needs PENNY_COGNITO_USER_POOL_ID and PENNY_COGNITO_APP_CLIENT_ID"
        )
    issuer = cognito_issuer(cfg.cognito_region, cfg.cognito_user_pool_id)
    return CognitoTokenVerifier(
        issuer=issuer,
        app_client_id=cfg.cognito_app_client_id,
        fetch_jwks=http_jwks_fetcher(f"{issuer}/.well-known/jwks.json"),
        refresh_min_seconds=cfg.jwks_refresh_min_seconds,
    )
