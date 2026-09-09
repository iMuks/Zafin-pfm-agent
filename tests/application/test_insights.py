"""The arithmetic Penny is trusted with.

These are the tests that matter most in this codebase. Every number the agent
states comes from this layer, so a silent defect here becomes a confidently
wrong financial statement to a customer — the exact failure mode the whole
tool-calling design exists to prevent.
"""

from __future__ import annotations

import unittest

from penny.application.insights.service import InsightService
from tests.support import repo, txn


class SpendingMaths(unittest.TestCase):
    def test_transfers_and_income_are_not_spending(self):
        """A $3,000 Venmo payment must not dwarf a month of real spending."""
        service = InsightService(
            repo(
                txn(100.0, "2026-07-02", category="Groceries"),
                txn(3000.0, "2026-07-03", category="Transfers", merchant="Venmo"),
                txn(4000.0, "2026-07-04", category="Income", direction="credit"),
            )
        )
        result = service.get_spending_by_category(month="2026-07")
        self.assertEqual(result["total"], 100.0)
        self.assertEqual(result["transaction_count"], 1)

    def test_weekly_average_uses_the_calendar_month_not_the_transaction_span(self):
        """Regression: two shops three days apart is not "a week" of spending.

        The earlier implementation divided by the span between the first and
        last matching transaction, so this case reported $140/week instead of
        $31.61/week — a roughly sevenfold overstatement on the assignment's own
        "average weekly grocery spend" user story.
        """
        service = InsightService(repo(txn(100.0, "2026-07-02"), txn(40.0, "2026-07-05")))
        result = service.get_spending_by_category(category="Groceries", month="2026-07")
        self.assertEqual(result["total"], 140.0)
        self.assertEqual(result["weeks_in_period"], 4.43)
        self.assertEqual(result["average_per_week"], 31.61)

    def test_category_filter_is_case_insensitive(self):
        service = InsightService(repo(txn(50.0, "2026-07-02", category="Dining")))
        self.assertEqual(service.get_spending_by_category(category="dining")["total"], 50.0)

    def test_unknown_category_returns_zero_rather_than_raising(self):
        service = InsightService(repo(txn(50.0, "2026-07-02")))
        self.assertEqual(service.get_spending_by_category(category="Crypto")["total"], 0.0)

    def test_top_categories_shares_sum_to_one_hundred(self):
        service = InsightService(
            repo(
                txn(60.0, "2026-07-02", category="Groceries"),
                txn(30.0, "2026-07-03", category="Dining"),
                txn(10.0, "2026-07-04", category="Fuel"),
            )
        )
        result = service.get_top_categories(month="2026-07", limit=3)
        self.assertEqual(result["total_spend"], 100.0)
        self.assertEqual([c["share_of_spend"] for c in result["categories"]], [60.0, 30.0, 10.0])

    def test_empty_month_does_not_divide_by_zero(self):
        service = InsightService(repo(txn(10.0, "2026-07-02")))
        result = service.get_top_categories(month="2026-01")
        self.assertEqual(result["total_spend"], 0.0)
        self.assertEqual(result["categories"], [])


class MerchantQueries(unittest.TestCase):
    def test_top_merchants_average_per_visit(self):
        service = InsightService(
            repo(
                txn(30.0, "2026-07-01", merchant="Starbucks", category="Dining"),
                txn(10.0, "2026-07-05", merchant="Starbucks", category="Dining"),
                txn(35.0, "2026-07-06", merchant="Shake Shack", category="Dining"),
            )
        )
        top = service.get_top_merchants(month="2026-07")["merchants"][0]
        self.assertEqual(top["merchant"], "Starbucks")
        self.assertEqual(top["total"], 40.0)
        self.assertEqual(top["visits"], 2)
        self.assertEqual(top["average_per_visit"], 20.0)

    def test_merchant_search_matches_the_raw_descriptor_too(self):
        service = InsightService(
            repo(txn(9.0, "2026-07-01", merchant="Dunkin'", description="DUNKIN #55 BOSTON MA"))
        )
        self.assertEqual(service.find_transactions(merchant="dunkin")["match_count"], 1)
        self.assertEqual(service.find_transactions(merchant="BOSTON")["match_count"], 1)

    def test_find_transactions_returns_newest_first_and_flags_truncation(self):
        service = InsightService(repo(*[txn(10.0, f"2026-07-{d:02d}") for d in range(1, 8)]))
        result = service.find_transactions(limit=3)
        self.assertEqual(result["shown"], 3)
        self.assertEqual(result["match_count"], 7)
        self.assertTrue(result["truncated"])
        self.assertEqual(result["transactions"][0]["date"], "2026-07-07")

    def test_location_reaches_the_public_payload(self):
        service = InsightService(
            repo(txn(9.0, "2026-07-01", merchant="Shake Shack", city="Atlanta", region="GA"))
        )
        self.assertEqual(service.find_transactions()["transactions"][0]["location"], "Atlanta, GA")


class Recurrence(unittest.TestCase):
    def test_a_single_charge_is_not_a_subscription(self):
        """One purchase at a subscription merchant must not be reported as one."""
        service = InsightService(
            repo(
                txn(
                    12.0,
                    "2026-07-01",
                    merchant="Netflix",
                    category="Subscriptions",
                    is_recurring=True,
                )
            )
        )
        self.assertEqual(service.list_subscriptions()["count"], 0)

    def test_frequently_visited_merchant_is_not_recurring(self):
        service = InsightService(
            repo(*[txn(50.0, f"2026-0{m}-05", merchant="Whole Foods") for m in (1, 2, 3)])
        )
        self.assertEqual(service.list_subscriptions()["count"], 0)

    def test_monthly_cadence_detected(self):
        service = InsightService(
            repo(
                *[
                    txn(
                        15.99,
                        f"2026-0{m}-14",
                        merchant="Hulu",
                        category="Subscriptions",
                        is_recurring=True,
                    )
                    for m in (4, 5, 6)
                ]
            )
        )
        subs = service.list_subscriptions()
        self.assertEqual(subs["count"], 1)
        self.assertEqual(subs["subscriptions"][0]["cadence"], "monthly")
        self.assertFalse(subs["subscriptions"][0]["amount_varies"])
        self.assertEqual(subs["estimated_monthly_cost"], 15.99)

    def test_variable_amounts_are_flagged(self):
        """The sample dataset randomises subscription amounts; say so, don't hide it."""
        service = InsightService(
            repo(
                txn(
                    6.59,
                    "2026-04-14",
                    merchant="Netflix",
                    category="Subscriptions",
                    is_recurring=True,
                ),
                txn(
                    52.45,
                    "2026-05-14",
                    merchant="Netflix",
                    category="Subscriptions",
                    is_recurring=True,
                ),
                txn(
                    19.99,
                    "2026-06-14",
                    merchant="Netflix",
                    category="Subscriptions",
                    is_recurring=True,
                ),
            )
        )
        self.assertTrue(service.list_subscriptions()["subscriptions"][0]["amount_varies"])


class Anomalies(unittest.TestCase):
    def test_duplicate_requires_same_merchant_amount_and_window(self):
        service = InsightService(
            repo(
                txn(22.99, "2026-07-01", merchant="Netflix", category="Subscriptions"),
                txn(22.99, "2026-07-02", merchant="Netflix", category="Subscriptions"),
            )
        )
        duplicates = service.detect_anomalies()["duplicate_charges"]
        self.assertEqual(len(duplicates), 1)
        self.assertEqual(duplicates[0]["days_apart"], 1)

    def test_same_amount_far_apart_is_not_a_duplicate(self):
        service = InsightService(
            repo(
                txn(22.99, "2026-07-01", merchant="Netflix", category="Subscriptions"),
                txn(22.99, "2026-07-20", merchant="Netflix", category="Subscriptions"),
            )
        )
        self.assertEqual(service.detect_anomalies()["duplicate_charges"], [])

    def test_different_amounts_same_day_are_not_duplicates(self):
        service = InsightService(
            repo(
                txn(193.67, "2026-07-12", merchant="Apple Cash", category="Shopping"),
                txn(3172.29, "2026-07-12", merchant="Apple Cash", category="Shopping"),
            )
        )
        self.assertEqual(service.detect_anomalies()["duplicate_charges"], [])

    def test_outliers_are_judged_against_their_own_category(self):
        """A $200 grocery run is normal; a $200 coffee is not."""
        groceries = [
            txn(a, f"2026-07-{i + 1:02d}", category="Groceries")
            for i, a in enumerate([180.0, 190.0, 200.0, 195.0, 185.0, 205.0])
        ]
        dining = [
            txn(a, f"2026-07-{i + 10:02d}", category="Dining", merchant="Coffee")
            for i, a in enumerate([5.0, 6.0, 5.5, 6.5, 5.2, 200.0])
        ]
        service = InsightService(repo(*groceries, *dining))

        flagged = service.detect_anomalies()["unusual_transactions"]
        self.assertEqual([f["category"] for f in flagged], ["Dining"])
        self.assertEqual(flagged[0]["amount"], 200.0)

    def test_small_categories_are_skipped(self):
        service = InsightService(repo(txn(10.0, "2026-07-01"), txn(9999.0, "2026-07-02")))
        self.assertEqual(service.detect_anomalies()["unusual_transactions"], [])


class Trends(unittest.TestCase):
    def test_comparison_reports_change_and_percentage(self):
        service = InsightService(repo(txn(100.0, "2026-05-10"), txn(150.0, "2026-06-10")))
        result = service.compare_periods("2026-05", "2026-06")
        self.assertEqual(result["change"], 50.0)
        self.assertEqual(result["percent_change"], 50.0)

    def test_growth_from_zero_is_not_infinite(self):
        service = InsightService(repo(txn(150.0, "2026-06-10")))
        self.assertIsNone(service.compare_periods("2026-05", "2026-06")["percent_change"])

    def test_partial_month_comparison_is_flagged_not_comparable(self):
        """Data stops on the 28th, so July is not comparable to a full June."""
        service = InsightService(repo(txn(100.0, "2026-06-10"), txn(80.0, "2026-07-28")))
        result = service.compare_periods("2026-06", "2026-07")
        self.assertTrue(result["month_a_complete"])
        self.assertFalse(result["month_b_complete"])
        self.assertFalse(result["comparable"])
        self.assertIsNotNone(result["caveat"])


class IncomeAndForecast(unittest.TestCase):
    def test_thin_income_is_flagged_as_incomplete(self):
        """Two payroll credits in six months cannot be someone's whole income."""
        spend = [txn(500.0, f"2026-0{m}-10") for m in range(1, 7)]
        service = InsightService(
            repo(*spend, txn(1000.0, "2026-03-08", category="Income", direction="credit"))
        )
        result = service.get_income_and_savings()
        self.assertFalse(result["income_looks_complete"])
        self.assertIn("another account", result["caveat"])

    def test_income_share_by_category(self):
        service = InsightService(
            repo(
                txn(1000.0, "2026-07-01", category="Income", direction="credit"),
                txn(250.0, "2026-07-05", category="Dining"),
            )
        )
        result = service.get_income_and_savings(month="2026-07")
        self.assertEqual(result["spend_share_of_income"]["Dining"], 25.0)

    def test_forecast_projects_spending_and_says_it_is_not_a_balance(self):
        service = InsightService(repo(txn(100.0, "2026-07-01"), txn(100.0, "2026-07-10")))
        result = service.forecast_month_end("2026-07")
        self.assertEqual(result["days_elapsed"], 10)
        self.assertEqual(result["days_in_month"], 31)
        self.assertEqual(result["daily_run_rate"], 20.0)
        self.assertEqual(result["projected_month_end_spend"], 620.0)
        self.assertIn("not an account balance", result["caveat"])

    def test_forecast_baseline_excludes_a_partial_leading_month(self):
        """Regression: a stub first month must not drag the baseline down.

        January here is a single day worth $10 against two full $1,000 months.
        Averaging all three gives $670 and reports July as *above* average;
        averaging only the complete months gives $1,000 and reports it *below*.
        On the shipped dataset this inverted the answer to "am I on track".
        """
        service = InsightService(
            repo(
                txn(10.0, "2026-01-31"),
                txn(1000.0, "2026-02-10"),
                txn(1000.0, "2026-03-10"),
                txn(310.0, "2026-04-10"),
            )
        )
        result = service.forecast_month_end("2026-04")
        self.assertEqual(result["prior_months_used"], ["2026-02", "2026-03"])
        self.assertEqual(result["prior_months_excluded_as_partial"], ["2026-01"])
        self.assertEqual(result["prior_month_average_spend"], 1000.0)
        self.assertLess(result["versus_prior_average"], 0, "April must read as below average")
        self.assertIn("2026-01", result["baseline_note"])

    def test_forecast_says_so_when_no_month_is_covered_in_full(self):
        """With no complete prior month there is no baseline — and none is invented."""
        service = InsightService(repo(txn(50.0, "2026-06-20"), txn(100.0, "2026-07-05")))
        result = service.forecast_month_end("2026-07")
        self.assertIsNone(result["prior_month_average_spend"])
        self.assertIsNone(result["versus_prior_average"])
        self.assertIn("no baseline", result["baseline_note"])

    def test_forecast_on_an_empty_month_reports_available_months(self):
        service = InsightService(repo(txn(10.0, "2026-07-01")))
        result = service.forecast_month_end("2026-02")
        self.assertIn("error", result)
        self.assertEqual(result["available_months"], ["2026-07"])


class Coverage(unittest.TestCase):
    def test_coverage_states_what_is_missing(self):
        service = InsightService(repo(txn(10.0, "2026-07-01")))
        result = service.get_data_coverage()
        self.assertEqual(result["treat_as_today"], "2026-07-01")
        for absent in ("balance", "budget", "savings goal", "credit limit"):
            self.assertIn(absent, result["note"])


if __name__ == "__main__":
    unittest.main()
