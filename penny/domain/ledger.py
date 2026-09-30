"""The ledger entities: exact money, tenant-scoped, versioned, never updated.

Amounts are `Decimal` here (design R1): the balance identity must hold to the
cent, and a float cannot promise that. The existing `Transaction` keeps its
float because it is a read projection at the boundary; `to_transaction`
performs that one conversion.

Rows are never updated in place (Supersession): a change inserts a new row
that supersedes the old one, and the old one is retired at the same commit.
A row is active at version V when `created_version <= V` and
`retired_version` is null or greater than V.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Literal
from uuid import uuid4

from penny.domain.errors import InvalidTransactionError
from penny.domain.models import Direction, Transaction
from penny.domain.taxonomy import canonical

TRANSFERS_CATEGORY = "Transfers"
CENT = Decimal("0.01")

AccountType = Literal["bank", "card", "receivable", "payable", "revenue", "expense"]
AccountSide = Literal["bank", "book"]
SourceKind = Literal["aggregator", "open_banking", "direct_bank", "accounting", "file"]
SourceState = Literal["linked", "syncing", "verified", "needs_reauth", "blocked", "disconnected"]
PostingStatus = Literal["posted", "pending"]


def new_id() -> str:
    return uuid4().hex


def money(value: object) -> Decimal:
    """Parse a money amount exactly and quantize to the cent.

    Strings and integers are exact; a float is converted through `str` so a
    2-decimal float round-trips exactly instead of dragging binary noise in.
    """
    try:
        amount = Decimal(str(value)) if isinstance(value, float) else Decimal(value)  # type: ignore[arg-type]
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise InvalidTransactionError(f"amount {value!r} is not a number") from exc
    if not amount.is_finite():
        raise InvalidTransactionError(f"amount {value!r} is not finite")
    return amount.quantize(CENT)


@dataclass(frozen=True, slots=True, kw_only=True)
class Tenant:
    id: str
    name: str
    contact_name: str
    contact_email: str
    reporting_currency: str = "CAD"
    timezone: str = "America/Toronto"


@dataclass(frozen=True, slots=True, kw_only=True)
class Account:
    id: str
    tenant_id: str
    name: str
    type: AccountType
    currency: str
    side: AccountSide = "bank"

    @property
    def is_bank_side(self) -> bool:
        return self.type in ("bank", "card")


@dataclass(frozen=True, slots=True, kw_only=True)
class Source:
    id: str
    tenant_id: str
    kind: SourceKind
    credentials_ref: str | None = None
    state: SourceState = "linked"
    coverage_through: date | None = None
    external_ref: str | None = None  # the aggregator's item id, never a credential


@dataclass(frozen=True, slots=True, kw_only=True)
class Posting:
    """One bank-side line. `amount` is a positive magnitude; `direction` carries the sign."""

    id: str = field(default_factory=new_id)
    tenant_id: str
    account_id: str
    source_id: str
    day: date
    amount: Decimal
    direction: Direction
    description: str
    merchant: str
    category: str
    confidence: float = 1.0
    row_hash: str = ""
    native_id: str | None = None
    status: PostingStatus = "posted"
    supersedes_id: str | None = None
    created_version: int | None = None
    retired_version: int | None = None
    is_recurring: bool = False
    logo_domain: str | None = None
    provenance: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.amount, Decimal) or not self.amount.is_finite():
            raise InvalidTransactionError(f"posting {self.id} amount must be a finite Decimal")
        if self.amount < 0:
            raise InvalidTransactionError(
                f"posting {self.id} amount must be a positive magnitude; direction carries the sign"
            )
        if self.direction not in ("debit", "credit"):
            raise InvalidTransactionError(f"posting {self.id} direction must be debit or credit")
        if canonical(self.category) is None:
            raise InvalidTransactionError(
                f"posting {self.id} has unknown category {self.category!r}"
            )

    @property
    def signed_amount(self) -> Decimal:
        """Credits add to a balance, debits take from it."""
        return self.amount if self.direction == "credit" else -self.amount

    def active_at(self, version: int) -> bool:
        return (
            self.created_version is not None
            and self.created_version <= version
            and (self.retired_version is None or self.retired_version > version)
        )


@dataclass(frozen=True, slots=True, kw_only=True)
class TransferPair:
    id: str = field(default_factory=new_id)
    tenant_id: str
    posting_a: str
    posting_b: str
    created_version: int | None = None
    retired_version: int | None = None


@dataclass(frozen=True, slots=True, kw_only=True)
class SyncRun:
    id: str = field(default_factory=new_id)
    tenant_id: str
    source_id: str
    sync_version: int | None = None
    started: datetime | None = None
    finished: datetime | None = None
    rows_in: int = 0
    rows_rejected: int = 0
    rows_skipped: int = 0
    rows_deduped: int = 0
    rows_retired: int = 0
    balance_check: str = "not_applicable"  # held | failed | not_applicable
    transfer_pairs_formed: int = 0
    warnings: tuple[str, ...] = ()
    status: str = "committed"


def balance_identity(opening: Decimal, postings: list[Posting], closing: Decimal) -> Decimal:
    """Opening plus the signed sum of every row minus closing; zero when the identity holds."""
    total = sum((p.signed_amount for p in postings), Decimal("0"))
    return (opening + total - closing).quantize(CENT)


def to_transaction(posting: Posting, *, in_transfer: bool) -> Transaction:
    """The read projection the ten insights consume.

    The one place money becomes a float. A posting that is one side of an
    active transfer pair is presented as `Transfers` without touching the
    stored category.
    """
    return Transaction(
        id=posting.id,
        day=posting.day,
        description=posting.description,
        amount=float(posting.amount),
        merchant=posting.merchant,
        category=TRANSFERS_CATEGORY if in_transfer else posting.category,
        direction=posting.direction,
        is_recurring=posting.is_recurring,
        logo_domain=posting.logo_domain,
        confidence=posting.confidence,
    )
