"""The entities every layer above agrees on.

`Transaction` is frozen. Analytics is a pipeline of filters and aggregations
over a shared list; if any stage could mutate a record, a later stage would see
a different dataset than the one it was reasoning about. Immutability makes the
whole read path safe to share and safe to cache.

The parsed `day` is stored on the entity rather than re-parsed at every
comparison: date arithmetic happens in nearly every insight, and
`date.fromisoformat` in an inner loop is the kind of cost that only shows up
once the dataset is real.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Any, Literal

from penny.domain.errors import InvalidTransactionError
from penny.domain.taxonomy import INCOME_CATEGORY, NON_SPEND_CATEGORIES, canonical

Direction = Literal["debit", "credit"]


@dataclass(frozen=True, slots=True)
class Transaction:
    """One enriched transaction.

    `description` is the raw bank descriptor as it appeared on the statement;
    `merchant` is the cleaned, customer-facing name produced by enrichment.
    Both are kept: the customer recognises the merchant, but an investigator
    needs the descriptor that actually posted.
    """

    id: str
    day: date
    description: str
    amount: float
    merchant: str
    category: str
    direction: Direction
    is_recurring: bool = False
    logo_domain: str | None = None
    city: str | None = None
    region: str | None = None
    confidence: float = 1.0

    # -- domain predicates ------------------------------------------------
    # Phrased as questions about the transaction, so that call sites read as
    # business rules ("if txn.is_spend") rather than as data comparisons.

    @property
    def is_spend(self) -> bool:
        """Money actually consumed: a debit that is not a transfer or income."""
        return self.direction == "debit" and self.category not in NON_SPEND_CATEGORIES

    @property
    def is_income(self) -> bool:
        return self.direction == "credit" and self.category == INCOME_CATEGORY

    @property
    def month(self) -> str:
        """Calendar month as "YYYY-MM" — the period key used everywhere."""
        return self.day.strftime("%Y-%m")

    @property
    def iso_date(self) -> str:
        return self.day.isoformat()

    @property
    def location(self) -> str | None:
        """ "Seattle, WA" when enrichment resolved a location, else None."""
        if self.city and self.region:
            return f"{self.city}, {self.region}"
        return self.city or self.region

    def matches_merchant(self, needle: str) -> bool:
        """Substring match over the clean name *and* the raw descriptor.

        Both, because a customer may search either what they remember seeing
        in the app ("Whole Foods") or what is printed on the statement.
        """
        probe = needle.casefold()
        return probe in self.merchant.casefold() or probe in self.description.casefold()

    # -- serialisation ----------------------------------------------------

    @classmethod
    def from_record(cls, record: dict[str, Any]) -> Transaction:
        """Build from an enriched JSON record, validating the domain invariants.

        Enrichment is an LLM pass, so its output is checked here rather than
        trusted. A record that violates an invariant fails loudly at load time,
        where it is one bad row in a file, instead of silently skewing a total
        the customer is later shown.
        """
        try:
            day = date.fromisoformat(str(record["date"]))
            amount = float(record["amount"])
            direction = str(record["direction"])
            category = canonical(str(record["category"]))
        except (KeyError, TypeError, ValueError) as exc:
            raise InvalidTransactionError(f"Malformed transaction record: {exc}") from exc

        if category is None:
            raise InvalidTransactionError(
                f"Unknown category {record.get('category')!r} in {record.get('id')!r}. "
                "Enrichment must emit a category from penny.domain.taxonomy."
            )
        if direction not in ("debit", "credit"):
            raise InvalidTransactionError(
                f"direction must be 'debit' or 'credit', got {direction!r}."
            )
        if amount < 0:
            raise InvalidTransactionError(
                f"amount must be a positive magnitude; direction carries the sign "
                f"(got {amount} in {record.get('id')!r})."
            )

        return cls(
            id=str(record["id"]),
            day=day,
            description=str(record.get("description", "")),
            amount=round(amount, 2),
            merchant=str(record.get("merchant") or record.get("description", "Unknown")),
            category=category,
            direction=direction,  # type: ignore[arg-type]
            is_recurring=bool(record.get("is_recurring", False)),
            logo_domain=record.get("logo_domain") or None,
            city=record.get("city") or None,
            region=record.get("region") or None,
            confidence=float(record.get("confidence", 1.0)),
        )

    def to_public(self) -> dict[str, Any]:
        """The shape a transaction takes when it crosses the tool boundary.

        Explicit rather than `asdict`: internal fields (parsed date, enrichment
        confidence) must not leak into a model's context window just because
        someone added a field to the dataclass.
        """
        return {
            "id": self.id,
            "date": self.iso_date,
            "merchant": self.merchant,
            "description": self.description,
            "amount": self.amount,
            "category": self.category,
            "direction": self.direction,
            "logo_domain": self.logo_domain,
            "location": self.location,
        }
