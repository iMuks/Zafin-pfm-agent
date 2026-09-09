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
            "ANTHROPIC_API_KEY is not set. Copy .env.example to .env and add your key."
        )


class InvalidTransactionError(PennyError):
    """A record in the enriched dataset does not satisfy the domain invariants."""

    status_code = 500
