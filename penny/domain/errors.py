"""Domain and application errors, each carrying the HTTP status it maps to.

The status code lives on the exception rather than in the route handler so that
a new failure mode cannot be added without deciding how it surfaces. The
presentation layer reads `status_code`; it never enumerates error types.
"""

from __future__ import annotations


class PennyError(RuntimeError):
    """Base class. The message is safe to show the caller."""

    status_code: int = 500


class DataNotEnrichedError(PennyError):
    """The enriched dataset is absent — Task 2 has not been run."""

    status_code = 503


class MissingCredentialsError(PennyError):
    status_code = 503

    def __init__(self) -> None:
        super().__init__(
            "ANTHROPIC_API_KEY is not set. Copy .env.example to .env and add your key, "
            "select a local model (PENNY_MODEL_KEY=local-qwen3-4b) to run without one, "
            "or a Bedrock model (PENNY_MODEL_KEY=bedrock-sonnet) signed by the task role."
        )


class MissingTokenError(PennyError):
    """No bearer token on a request that requires one."""

    status_code = 401

    def __init__(self) -> None:
        super().__init__("Sign in to continue.")


class InvalidTokenError(PennyError):
    """The bearer token failed verification. The reason is logged, never returned."""

    status_code = 401

    def __init__(self) -> None:
        super().__init__("Sign in again to continue.")


class JwksUnavailableError(PennyError):
    """The key set could not be fetched and nothing is cached: a 503, never a 401.

    A 401 here would lie: the token may be perfectly valid, and the client
    would send the customer back to sign in for a fault on the server side.
    """

    status_code = 503

    def __init__(self, retry_after_seconds: int = 30) -> None:
        self.retry_after_seconds = retry_after_seconds
        super().__init__("Penny can't verify sign-ins right now. Try again in a minute.")


class RemoteModelForbiddenError(PennyError):
    """The enclave rule: a `local` model must be served from inside the deployment.

    Raised when a local-provider entry names a model that would run elsewhere
    (an Ollama `:cloud` model, for example) or when the configured endpoint is
    not a loopback or private-network host. This is a configuration error and it
    is fatal on purpose: silently falling back to a remote model would send
    customer data outside the boundary the product promises.
    """

    status_code = 503

    def __init__(self, reason: str) -> None:
        super().__init__(f"Refusing a model that would run outside the enclave: {reason}")


class InvalidTransactionError(PennyError):
    """A record in the enriched dataset does not satisfy the domain invariants."""

    status_code = 500
