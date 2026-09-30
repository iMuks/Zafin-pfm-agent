"""Column types that keep money exact on both engines."""

from __future__ import annotations

from decimal import Decimal

from sqlalchemy import Numeric, String
from sqlalchemy.types import TypeDecorator


class Money(TypeDecorator):
    """NUMERIC(18,2) on Postgres; TEXT-encoded Decimal on SQLite.

    SQLite's NUMERIC affinity stores cents as a REAL, which loses the exactness
    the balance identity depends on, so there the value travels as its own
    string and is parsed back into a Decimal.
    """

    impl = Numeric(18, 2)
    cache_ok = True

    def load_dialect_impl(self, dialect):
        if dialect.name == "sqlite":
            return dialect.type_descriptor(String(32))
        return dialect.type_descriptor(Numeric(18, 2))

    def process_bind_param(self, value, dialect):
        if value is None:
            return None
        amount = value if isinstance(value, Decimal) else Decimal(str(value))
        return str(amount.quantize(Decimal("0.01"))) if dialect.name == "sqlite" else amount

    def process_result_value(self, value, dialect):
        if value is None:
            return None
        return Decimal(str(value)).quantize(Decimal("0.01"))
