"""Who is asking: the identity port the HTTP layer resolves a token through."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable


@dataclass(frozen=True, slots=True)
class Identity:
    """A verified caller. `poid` is the pseudonymous id every session and audit
    record is scoped to; in the deployment it is the user pool subject."""

    poid: str
    subject: str
    token_use: str


@runtime_checkable
class TokenVerifier(Protocol):
    """Verify a bearer token or raise one of the auth errors in `penny.domain.errors`.

    `status` reports whether the verifier can currently do its job (`ok`) or is
    missing its key set (`unavailable`); the liveness probe returns it.
    """

    status: str

    def verify(self, token: str) -> Identity: ...
