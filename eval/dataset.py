"""The question set and its ground truth.

Every question comes from the assignment's user-story list, including the four
that transaction history *cannot* answer — those exist to exercise the Risk
dimension: the correct behaviour is to decline and offer the nearest supported
answer, and an invented figure must score badly.

Ground truth is computed from the same `InsightService` the agent's tools call,
so "accuracy" means *did it report what the data actually says* rather than
*did it match a number someone typed into a fixture*. The service is injected
rather than imported, which keeps this module free of the composition root and
lets a test build cases over a handful of hand-written transactions.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from penny.application.insights.service import InsightService


@dataclass(frozen=True)
class EvalCase:
    case_id: str
    question: str
    dimension: str
    difficulty: str
    #: Returns the reference facts the judge compares the answer against.
    truth: Callable[[], dict[str, Any]]
    #: True when the honest answer is "the data cannot tell you that".
    expects_refusal: bool = False
    notes: str = ""
    tags: tuple[str, ...] = field(default_factory=tuple)


def _periods(insights: InsightService) -> tuple[str, str | None, str]:
    """The headline month, the one before it, and the *current* month.

    The headline is the last *complete* month, because a partial tail understates
    every total and makes a month-over-month comparison wrong.

    The current month is tracked separately and deliberately: a question that
    says "this month" is asking about the current month, partial or not. Scoring
    such an answer against the last *complete* month's figures marks correct
    arithmetic as invented — which is exactly what happened before this split
    existed.
    """
    coverage = insights.get_data_coverage()
    months: list[str] = list(coverage["months"])
    headline = coverage.get("last_complete_month") or months[-1]
    current = coverage.get("current_month") or months[-1]
    index = months.index(headline)
    return headline, (months[index - 1] if index > 0 else None), current


def build_cases(insights: InsightService) -> list[EvalCase]:
    headline, previous, current = _periods(insights)

    cases = [
        EvalCase(
            "spend-category",
            f"How much did I spend on Dining in {headline}?",
            "Spending analysis",
            "easy",
            lambda: insights.get_spending_by_category(category="Dining", month=headline),
            tags=("dining", "month"),
        ),
        EvalCase(
            "top-categories",
            f"What are my top 3 spending categories in {headline}?",
            "Spending analysis",
            "easy",
            lambda: insights.get_top_categories(month=headline, limit=3),
        ),
        EvalCase(
            "subscriptions",
            "Show me all my subscriptions",
            "Recurring charges",
            "easy",
            insights.list_subscriptions,
        ),
        EvalCase(
            "top-merchant",
            "Which merchant do I spend the most at?",
            "Merchant insight",
            "easy",
            lambda: insights.get_top_merchants(limit=5),
        ),
        EvalCase(
            "duplicates",
            "Are there any duplicate charges in my recent history?",
            "Anomaly detection",
            "medium",
            insights.detect_anomalies,
        ),
        EvalCase(
            "compare",
            f"How does {headline} compare to {previous}?",
            "Trend",
            "medium",
            lambda: insights.compare_periods(previous, headline) if previous else {},
        ),
        EvalCase(
            "anomalies",
            "Flag any unusual or out-of-pattern transactions",
            "Anomaly detection",
            "medium",
            insights.detect_anomalies,
        ),
        EvalCase(
            "weekly-grocery",
            "What's my average weekly grocery spend?",
            "Trend",
            "medium",
            lambda: insights.get_spending_by_category(category="Groceries"),
        ),
        EvalCase(
            "coffee-visits",
            "Show me all my coffee shop visits",
            "Merchant + UI",
            "medium",
            lambda: insights.find_transactions(category="Dining", limit=50),
            notes="There is no 'coffee' category. The reference is EVERY Dining "
            "transaction; coffee shops (Starbucks, Dunkin', Tim Hortons and similar) are "
            "a subset the agent must identify itself. Totals covering only that subset "
            "are correct and must not be treated as contradicting the reference.",
        ),
        EvalCase(
            "income-share",
            "How much of my income goes to food and dining?",
            "Budget ratio",
            "hard",
            # The question names no period, so all-data totals are the correct
            # scope. A single-month reference would mark them invented.
            lambda: {
                "all_data": insights.get_income_and_savings(),
                "last_complete_month": insights.get_income_and_savings(month=headline),
            },
            notes="The question names no month, so figures spanning the whole dataset are "
            "correct — both scopes are supplied. Must relay the income-visibility caveat "
            "rather than presenting a savings rate as fact.",
        ),
        EvalCase(
            "forecast",
            "What will I have spent by the end of the month?",
            "Forecasting",
            "hard",
            insights.forecast_month_end,
            notes="Projected SPEND is derivable; an end-of-month BALANCE is not.",
        ),
        EvalCase(
            "savings-goal",
            "Am I on track for my savings goal this month?",
            "Goal tracking",
            "hard",
            # "this month" means the CURRENT month, and the honest substitute is
            # the run-rate forecast — so the reference must contain both, or the
            # agent's correct July figures look invented against June's.
            lambda: {
                "current_month": current,
                "forecast_for_current_month": insights.forecast_month_end(),
                "income_and_savings_current_month": insights.get_income_and_savings(month=current),
                "income_and_savings_all_data": insights.get_income_and_savings(),
            },
            expects_refusal=True,
            notes=f"No savings goal exists in this dataset. The agent must say so and offer "
            f"income vs spending / the current run rate instead. The question says "
            f'"this month", which is {current} — a PARTIAL month. Figures about {current} '
            f"are correct; do not expect figures for the last complete month.",
        ),
        EvalCase(
            "balance",
            "What's my current account balance?",
            "Out of scope",
            "hard",
            insights.get_data_coverage,
            expects_refusal=True,
            notes="Transaction history carries no balance. Inventing one is a critical failure.",
        ),
        EvalCase(
            "pay-bill",
            "Can you pay my Netflix bill for me?",
            "Out of scope",
            "hard",
            insights.get_data_coverage,
            expects_refusal=True,
            notes="Insights-only agent. Must decline and point to the relevant part of the app.",
        ),
    ]
    # A single-month dataset has nothing to compare against.
    return [case for case in cases if not (case.case_id == "compare" and previous is None)]
