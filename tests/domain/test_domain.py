"""Domain invariants: the rules that must hold before any use case runs."""

from __future__ import annotations

import unittest
from datetime import date

from penny.domain.errors import InvalidTransactionError
from penny.domain.models import Transaction
from penny.domain.periods import (
    Period,
    days_in_month,
    is_month_complete,
    is_month_fully_covered,
    last_complete_month,
    parse_month,
    previous_month,
)
from penny.domain.taxonomy import CATEGORIES, NON_SPEND_CATEGORIES, canonical, is_valid

BASE = {
    "id": "txn_0001",
    "date": "2026-07-04",
    "description": "WHOLE FOODS SEATTLE WA",
    "amount": 63.7,
    "merchant": "Whole Foods Market",
    "category": "Groceries",
    "direction": "debit",
}


class TransactionInvariants(unittest.TestCase):
    def test_valid_record_parses(self):
        t = Transaction.from_record({**BASE, "city": "Seattle", "region": "WA"})
        self.assertEqual(t.month, "2026-07")
        self.assertEqual(t.location, "Seattle, WA")
        self.assertTrue(t.is_spend)

    def test_category_is_canonicalised(self):
        self.assertEqual(
            Transaction.from_record({**BASE, "category": "groceries"}).category, "Groceries"
        )

    def test_unknown_category_is_rejected(self):
        """Enrichment is an LLM pass, so its output is checked rather than trusted."""
        with self.assertRaises(InvalidTransactionError):
            Transaction.from_record({**BASE, "category": "Crypto"})

    def test_negative_amount_is_rejected(self):
        """Direction carries the sign; a negative magnitude means a broken pipeline."""
        with self.assertRaises(InvalidTransactionError):
            Transaction.from_record({**BASE, "amount": -10.0})

    def test_bad_direction_is_rejected(self):
        with self.assertRaises(InvalidTransactionError):
            Transaction.from_record({**BASE, "direction": "outgoing"})

    def test_malformed_date_is_rejected(self):
        with self.assertRaises(InvalidTransactionError):
            Transaction.from_record({**BASE, "date": "04/07/2026"})

    def test_transaction_is_immutable(self):
        t = Transaction.from_record(BASE)
        with self.assertRaises((AttributeError, TypeError)):
            t.amount = 1.0  # type: ignore[misc]

    def test_transfers_and_income_are_not_spend(self):
        self.assertFalse(Transaction.from_record({**BASE, "category": "Transfers"}).is_spend)
        income = Transaction.from_record({**BASE, "category": "Income", "direction": "credit"})
        self.assertFalse(income.is_spend)
        self.assertTrue(income.is_income)

    def test_public_payload_hides_internal_fields(self):
        public = Transaction.from_record({**BASE, "confidence": 0.4}).to_public()
        self.assertNotIn("confidence", public)
        self.assertNotIn("is_recurring", public)
        self.assertNotIn("day", public)


class Taxonomy(unittest.TestCase):
    def test_categories_are_unique_and_closed(self):
        self.assertEqual(len(CATEGORIES), len(set(CATEGORIES)))
        self.assertTrue(set(CATEGORIES) >= NON_SPEND_CATEGORIES)

    def test_case_insensitive_resolution(self):
        self.assertEqual(canonical("  health & pharmacy "), "Health & Pharmacy")
        self.assertIsNone(canonical("Restaurants"))
        self.assertTrue(is_valid("dining"))


class Periods(unittest.TestCase):
    def test_month_period_covers_the_whole_calendar_month(self):
        p = Period.for_month("2026-07")
        self.assertEqual((p.start, p.end), (date(2026, 7, 1), date(2026, 7, 31)))
        self.assertEqual(p.days, 31)

    def test_february_leap_year(self):
        self.assertEqual(days_in_month("2024-02"), 29)
        self.assertEqual(days_in_month("2026-02"), 28)

    def test_weeks_never_below_one(self):
        """A one-day period must not turn a daily total into a huge weekly average."""
        self.assertEqual(Period(date(2026, 7, 1), date(2026, 7, 1)).weeks, 1.0)

    def test_previous_month_crosses_the_year(self):
        self.assertEqual(previous_month("2026-01"), "2025-12")
        self.assertEqual(previous_month("2026-07"), "2026-06")

    def test_completeness_is_about_the_final_calendar_day(self):
        self.assertFalse(is_month_complete("2026-07", date(2026, 7, 28)))
        self.assertTrue(is_month_complete("2026-07", date(2026, 7, 31)))
        self.assertTrue(is_month_complete("2026-06", date(2026, 7, 28)))

    def test_full_coverage_also_checks_the_leading_edge(self):
        """Regression: `is_month_complete` only ever looked at the trailing edge.

        The sample dataset opens on 2026-01-28, so January holds four days — yet
        it reaches its own month end and so passed `is_month_complete`. Averaging
        that stub in as a whole month understated the spending baseline by
        $1,892.74 and flipped "am I on track" from under to over.
        """
        first, latest = date(2026, 1, 28), date(2026, 7, 28)
        self.assertTrue(is_month_complete("2026-01", latest), "the old, one-sided check")
        self.assertFalse(is_month_fully_covered("2026-01", first, latest), "the leading stub")
        self.assertTrue(is_month_fully_covered("2026-02", first, latest))
        self.assertTrue(is_month_fully_covered("2026-06", first, latest))
        self.assertFalse(is_month_fully_covered("2026-07", first, latest), "the trailing stub")

    def test_last_complete_month_skips_a_partial_tail(self):
        months = ["2026-05", "2026-06", "2026-07"]
        self.assertEqual(last_complete_month(months, date(2026, 7, 28)), "2026-06")
        self.assertEqual(last_complete_month(months, date(2026, 7, 31)), "2026-07")

    def test_invalid_month_raises(self):
        for bad in ("2026-13", "July", "2026"):
            with self.assertRaises(ValueError):
                parse_month(bad)

    def test_period_rejects_reversed_bounds(self):
        with self.assertRaises(ValueError):
            Period(date(2026, 7, 10), date(2026, 7, 1))


if __name__ == "__main__":
    unittest.main()
