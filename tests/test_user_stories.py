"""Requirements traceability: the twelve user stories from the assignment brief.

Each test names one story verbatim and asserts that the tool layer can actually
answer it **against the real shipped dataset**, not a fixture. This is the
executable version of the coverage table in the README: if a change breaks the
agent's ability to answer "show me all my subscriptions", a test named for that
story fails.

The last three stories are the ones the brief flags as "not every story is
achievable with transaction history alone". They are tested for the *right*
behaviour, which is an honest boundary plus the nearest supported answer — not
for a fabricated number.
"""

from __future__ import annotations

import re
import unittest
from pathlib import Path

from penny.application.insights.service import InsightService
from penny.infrastructure.persistence.json_transactions import JsonTransactionRepository

ROOT = Path(__file__).resolve().parents[1]
DATASET = ROOT / "data" / "transactions_enriched.json"


@unittest.skipUnless(DATASET.exists(), "run scripts/enrich_transactions.py first")
class UserStories(unittest.TestCase):
    """Every story the agent claims to support, exercised end to end."""

    @classmethod
    def setUpClass(cls):
        cls.repo = JsonTransactionRepository(DATASET)
        cls.insights = InsightService(cls.repo)
        cls.coverage = cls.insights.get_data_coverage()
        cls.latest_month = cls.coverage["current_month"]
        cls.complete_month = cls.coverage["last_complete_month"]

    # -- easy -------------------------------------------------------------

    def test_how_much_did_i_spend_on_restaurants_last_month(self):
        result = self.insights.get_spending_by_category(
            category="Dining", month=self.complete_month
        )
        self.assertGreater(result["total"], 0)
        self.assertEqual(result["category"], "Dining")
        self.assertGreater(result["transaction_count"], 0)

    def test_what_are_my_top_3_spending_categories_this_month(self):
        result = self.insights.get_top_categories(month=self.latest_month, limit=3)
        self.assertEqual(len(result["categories"]), 3)
        totals = [c["total"] for c in result["categories"]]
        self.assertEqual(totals, sorted(totals, reverse=True), "categories must be ranked")
        self.assertLessEqual(sum(c["share_of_spend"] for c in result["categories"]), 100.5)

    def test_show_me_all_my_subscriptions(self):
        result = self.insights.list_subscriptions()
        self.assertGreater(result["count"], 0)
        merchants = {s["merchant"] for s in result["subscriptions"]}
        # The dataset carries well-known subscription merchants; at least some
        # must survive the "flagged AND charged twice" rule.
        self.assertTrue(merchants)
        for sub in result["subscriptions"]:
            self.assertGreaterEqual(sub["charge_count"], 2)
            self.assertIn(sub["cadence"], {"weekly", "monthly", "quarterly", "annual", "irregular"})
        # Bank fees repeat but are not subscriptions.
        self.assertNotIn("Fees & Charges", {s["category"] for s in result["subscriptions"]})

    def test_which_merchant_do_i_spend_the_most_at(self):
        result = self.insights.get_top_merchants(limit=5)
        self.assertEqual(len(result["merchants"]), 5)
        top = result["merchants"][0]
        self.assertGreater(top["total"], 0)
        self.assertGreaterEqual(top["visits"], 1)
        self.assertAlmostEqual(top["total"] / top["visits"], top["average_per_visit"], places=1)

    # -- medium -----------------------------------------------------------

    def test_are_there_any_duplicate_charges_in_my_recent_history(self):
        result = self.insights.detect_anomalies()
        self.assertIn("duplicate_charges", result)
        self.assertIn("method", result)
        for duplicate in result["duplicate_charges"]:
            self.assertLessEqual(duplicate["days_apart"], 3)
            self.assertEqual(len(duplicate["transaction_ids"]), 2)

    def test_how_does_my_spending_this_month_compare_to_last_month(self):
        months = self.repo.months()
        result = self.insights.compare_periods(months[-2], months[-1])
        self.assertIn("change", result)
        # The dataset stops mid-July, so this comparison must declare itself
        # not like-for-like rather than quoting a misleading percentage.
        self.assertFalse(result["comparable"])
        self.assertIsNotNone(result["caveat"])

    def test_flag_any_unusual_or_out_of_pattern_transactions(self):
        result = self.insights.detect_anomalies()
        self.assertIn("unusual_transactions", result)
        for flagged in result["unusual_transactions"]:
            self.assertGreater(flagged["amount"], flagged["category_average"])
        self.assertIn("not fraud findings", result["caveat"])

    def test_whats_my_average_weekly_grocery_spend(self):
        result = self.insights.get_spending_by_category(category="Groceries")
        self.assertGreater(result["average_per_week"], 0)
        # The denominator must be the calendar period, not the transaction span.
        expected = round(result["total"] / result["weeks_in_period"], 2)
        self.assertAlmostEqual(result["average_per_week"], expected, places=2)
        self.assertGreater(result["weeks_in_period"], 20, "six months is ~26 weeks")

    def test_show_me_all_coffee_shop_visits_with_their_logos(self):
        """Bonus A: the merchant cards this renders must carry logo domains."""
        result = self.insights.find_transactions(category="Dining", limit=50)
        self.assertGreater(result["match_count"], 0)
        with_logos = [t for t in result["transactions"] if t["logo_domain"]]
        self.assertGreater(len(with_logos), 0, "dining transactions should resolve logos")

    # -- hard: answerable with a stated caveat ----------------------------

    def test_how_much_of_my_income_goes_to_food_and_dining(self):
        result = self.insights.get_income_and_savings()
        self.assertIn("Dining", result["spend_share_of_income"])
        # This dataset shows only two payroll credits in six months, so the
        # ratio must be presented as partial rather than as a savings rate.
        self.assertFalse(result["income_looks_complete"])
        self.assertIn("another account", result["caveat"])

    def test_predict_my_likely_end_of_month_balance(self):
        """Answered as projected SPEND, with the balance limitation stated."""
        result = self.insights.forecast_month_end(self.latest_month)
        self.assertIn("projected_month_end_spend", result)
        self.assertNotIn("balance", set(result) - {"caveat"})
        self.assertIn("not an account balance", result["caveat"])
        self.assertIn("no opening balance", result["caveat"])

    # -- hard: correctly refused ------------------------------------------

    def test_am_i_on_track_for_my_savings_goal_this_month(self):
        """There is no goal in the data. The agent must say so.

        The brief calls this out explicitly: recognising which queries the data
        cannot support is part of the exercise. The guarantee is structural —
        no tool returns a savings goal, and coverage names the absence — so the
        model has nothing to hallucinate from and a documented fallback to offer.
        """
        coverage = self.insights.get_data_coverage()
        self.assertIn("savings goal", coverage["note"])
        self.assertIn("no budget", coverage["note"])

        tool_names = {"get_savings_goal", "get_balance", "get_budget"}
        from penny.application.tools.catalog import build_catalog

        available = {spec.name for spec in build_catalog(self.insights)}
        self.assertEqual(available & tool_names, set(), "no tool may invent a goal or balance")

        # The nearest honest answer is available instead.
        fallback = self.insights.get_income_and_savings(month=self.latest_month)
        self.assertIn("income", fallback)
        self.assertIn("spending", fallback)


@unittest.skipUnless(DATASET.exists(), "run scripts/enrich_transactions.py first")
class DatasetIntegrity(unittest.TestCase):
    """Guards on the shipped data itself — Task 1 and Task 2 acceptance."""

    @classmethod
    def setUpClass(cls):
        cls.repo = JsonTransactionRepository(DATASET)
        cls.transactions = list(cls.repo.all())

    def test_every_row_from_the_source_csv_is_present(self):
        source = ROOT / "data" / "sample_transactions.csv"
        with source.open(encoding="utf-8") as handle:
            expected = sum(1 for _ in handle) - 1  # minus the header row
        self.assertEqual(len(self.transactions), expected)

    def test_every_transaction_has_a_clean_merchant_and_category(self):
        """Task 2's minimum bar: a clean merchant name and a spending category."""
        for t in self.transactions:
            self.assertTrue(t.merchant.strip(), f"{t.id} has no merchant")
            self.assertNotEqual(t.merchant, t.description, f"{t.id} merchant was not cleaned")
            self.assertTrue(t.category)

    def test_no_processor_noise_survived_into_merchant_names(self):
        """Store numbers, terminal ids and processor prefixes must be stripped.

        Note the rule is a run of four or more digits, not "any digit":
        "Microsoft 365" is a brand name, while "MCDONALDS F3972" is noise.
        """
        for t in self.transactions:
            self.assertNotIn("*", t.merchant, f"{t.id}: {t.merchant}")
            self.assertNotIn("#", t.merchant, f"{t.id}: {t.merchant}")
            self.assertIsNone(
                re.search(r"\d{4,}", t.merchant),
                f"{t.id}: {t.merchant!r} still carries a reference number",
            )

    def test_a_merchant_spans_categories_only_via_distinct_descriptors(self):
        """Regression guard for the Metro/MetroPCS split.

        A merchant legitimately spanning two categories is normal — "TARGET
        GROCERY SEATTLE WA" is a grocery run while "TARGET #12 PHOENIX AZ" is
        general merchandise, and a real enrichment platform separates those by
        sub-banner. What is *not* legitimate is the same descriptor resolving
        two ways, which is what happened when "METRO # ..." came back as both
        the grocer and MetroPCS: that silently splits a `GROUP BY merchant`.

        So the invariant is not "one merchant, one category" — it is that each
        category a merchant carries must trace to its own descriptor stem.
        """
        stems: dict[str, dict[str, set[str]]] = {}
        for t in self.transactions:
            stem = re.sub(r"\d+", "#", t.description).strip()
            stems.setdefault(t.merchant, {}).setdefault(t.category, set()).add(stem)

        for merchant, by_category in stems.items():
            if len(by_category) < 2:
                continue
            groups = list(by_category.items())
            for i, (cat_a, stems_a) in enumerate(groups):
                for cat_b, stems_b in groups[i + 1 :]:
                    self.assertEqual(
                        stems_a & stems_b,
                        set(),
                        f"{merchant}: identical descriptors resolved to both "
                        f"{cat_a} and {cat_b} — {sorted(stems_a & stems_b)}",
                    )

    def test_no_two_merchant_names_differ_only_by_a_trailing_word(self):
        """Regression guard for the Shell / "Shell Oil" split.

        Distinct brands that share a prefix are fine and expected — Amazon vs
        Amazon Prime, Uber vs Uber Eats, Walmart vs Walmart Pharmacy. The defect
        is two names for the *same* descriptor family, which reconciliation
        collapses. This asserts that no such pair survives in the same category.
        """
        by_category: dict[str, set[str]] = {}
        for t in self.transactions:
            by_category.setdefault(t.category, set()).add(t.merchant)

        known_distinct = {("Wire Transfer", "Wire Transfer Fee")}
        for category, names in by_category.items():
            for a in names:
                for b in names:
                    if a == b or (a, b) in known_distinct:
                        continue
                    normalised_a = a.replace(" ", "").casefold()
                    normalised_b = b.replace(" ", "").casefold()
                    self.assertFalse(
                        normalised_b.startswith(normalised_a),
                        f"{category}: {a!r} and {b!r} look like the same brand twice",
                    )

    def test_dataset_spans_about_six_months(self):
        first, last = self.repo.date_bounds()
        self.assertGreaterEqual((last - first).days, 150)
        self.assertEqual(len(self.repo.months()), 7)

    def test_income_is_present_and_excluded_from_spending(self):
        self.assertTrue(any(t.is_income for t in self.transactions), "brief promises income rows")
        self.assertFalse(any(t.is_income and t.is_spend for t in self.transactions))

    def test_logo_coverage_is_high_enough_for_the_ui(self):
        """Bonus A depends on this: cards without a domain fall back to a monogram."""
        with_logo = sum(1 for t in self.transactions if t.logo_domain)
        self.assertGreater(with_logo / len(self.transactions), 0.8)


if __name__ == "__main__":
    unittest.main()
