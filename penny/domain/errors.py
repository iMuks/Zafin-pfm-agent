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
            "or select a local model (PENNY_MODEL_KEY=local-qwen3-4b) to run without one."
        )


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
