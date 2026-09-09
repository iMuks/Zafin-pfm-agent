"""The ten tools, bound to an `InsightService`.

Every schema below mirrors exactly one method on the service. The descriptions
are prompt engineering, not documentation: each says when to reach for the tool,
what the result contains, and — where it matters — what the model is obliged to
do with the result (relay a caveat, refuse to invent a balance).

Adding an insight is a two-step change with no third place to forget: add the
method to `InsightService`, add a `ToolSpec` here. The registry, the LangChain
adapter, the validator and the loading labels all derive from this list.
"""

from __future__ import annotations

from typing import Any

from penny.application.insights.service import InsightService
from penny.application.tools.registry import ToolSpec
from penny.domain.taxonomy import CATEGORIES

# Reused fragments, so a change to how a month is expressed happens once.
_MONTH: dict[str, Any] = {
    "type": "string",
    "description": 'Calendar month as "YYYY-MM", e.g. "2026-07". Omit for all available data.',
}
_CATEGORY: dict[str, Any] = {
    "type": "string",
    "enum": list(CATEGORIES),
    "description": "Spending category. Must be one of the listed values. Omit for all categories.",
}


def _object(properties: dict[str, Any], required: list[str] | None = None) -> dict[str, Any]:
    schema: dict[str, Any] = {
        "type": "object",
        "properties": properties,
        "additionalProperties": False,
    }
    if required:
        schema["required"] = required
    return schema


def build_catalog(insights: InsightService) -> list[ToolSpec]:
    """Bind the tool schemas to a concrete service instance."""
    return [
        ToolSpec(
            name="get_data_coverage",
            description=(
                "Report what data exists: the date range, which months are available, "
                "which month is the last COMPLETE one, the categories present, and "
                "explicitly what is NOT in the dataset. Call this first whenever a "
                "question depends on 'now' or 'this month', names a month you are not "
                "sure exists, or asks for something that may not be derivable from "
                "transactions at all — a balance, a budget, a savings goal, a credit "
                "limit. Answering one of those without checking here is how you end up "
                "inventing a number."
            ),
            input_schema=_object({}),
            handler=insights.get_data_coverage,
        ),
        ToolSpec(
            name="get_spending_by_category",
            description=(
                "Total spending for one category (or all categories) over a month or the "
                "whole history. Returns the total, transaction count, average per "
                "transaction, average per week, the number of weeks the average was taken "
                "over, and the top merchants behind it. Use for 'how much did I spend on "
                "X', and for weekly or per-visit averages."
            ),
            input_schema=_object({"category": _CATEGORY, "month": _MONTH}),
            handler=insights.get_spending_by_category,
        ),
        ToolSpec(
            name="get_top_categories",
            description=(
                "Rank spending categories by total, each with its share of total spend. "
                "Use for 'what are my top N categories' and 'where is my money going'."
            ),
            input_schema=_object(
                {
                    "month": _MONTH,
                    "limit": {"type": "integer", "minimum": 1, "maximum": 19, "default": 3},
                }
            ),
            handler=insights.get_top_categories,
        ),
        ToolSpec(
            name="get_top_merchants",
            description=(
                "Rank individual merchants by total spend, with visit counts and average "
                "per visit. Use for 'which merchant do I spend the most at', or to break a "
                "category down into the specific places behind it."
            ),
            input_schema=_object(
                {
                    "month": _MONTH,
                    "category": _CATEGORY,
                    "limit": {"type": "integer", "minimum": 1, "maximum": 20, "default": 5},
                }
            ),
            handler=insights.get_top_merchants,
        ),
        ToolSpec(
            name="list_subscriptions",
            description=(
                "Every recurring charge, with cadence, typical amount, total paid to date "
                "and an estimated monthly subscription cost. A merchant qualifies only if "
                "enrichment flagged it as subscription-like AND it has charged at least "
                "twice — bank fees recur but are not subscriptions and are excluded. Use "
                "for 'show me all my subscriptions' and 'what am I paying for monthly'. "
                "Where a subscription reports amount_varies, say the amount is not stable "
                "instead of quoting the median as if it were the price."
            ),
            input_schema=_object({}),
            handler=insights.list_subscriptions,
        ),
        ToolSpec(
            name="compare_periods",
            description=(
                "Compare two months side by side, overall and per category, with absolute "
                "and percentage change. Use for 'this month vs last month' and any up/down "
                "trend question. The result reports whether each month is complete: if "
                "'comparable' is false you must say the comparison is not like-for-like "
                "before quoting any percentage."
            ),
            input_schema=_object(
                {
                    "month_a": {**_MONTH, "description": 'The EARLIER month, "YYYY-MM".'},
                    "month_b": {**_MONTH, "description": 'The LATER month, "YYYY-MM".'},
                    "category": _CATEGORY,
                },
                required=["month_a", "month_b"],
            ),
            handler=insights.compare_periods,
        ),
        ToolSpec(
            name="detect_anomalies",
            description=(
                "Find duplicate charges (same merchant and amount within 3 days) and "
                "statistical outliers (more than 2 standard deviations above the mean for "
                "their own category). Use for 'anything unusual', 'was I charged twice', "
                "'flag out-of-pattern transactions'. Always relay the method: these are "
                "statistical flags, not fraud findings, and the customer should be told "
                "why a charge was flagged so they can judge it themselves."
            ),
            input_schema=_object({"month": _MONTH}),
            handler=insights.detect_anomalies,
        ),
        ToolSpec(
            name="find_transactions",
            description=(
                "List individual transactions matching a merchant name, category, month or "
                "minimum amount, newest first. Use when the customer wants to SEE the "
                "transactions rather than a total — 'show me my coffee shop visits', 'list "
                "everything over $200'. The app renders these as merchant cards with logos "
                "automatically, so call this and summarise, rather than retyping rows into "
                "your own answer."
            ),
            input_schema=_object(
                {
                    "merchant": {
                        "type": "string",
                        "description": "Merchant name; partial, case-insensitive match.",
                    },
                    "category": _CATEGORY,
                    "month": _MONTH,
                    "min_amount": {
                        "type": "number",
                        "description": "Only transactions at or above this amount.",
                    },
                    "limit": {"type": "integer", "minimum": 1, "maximum": 50, "default": 20},
                }
            ),
            handler=insights.find_transactions,
        ),
        ToolSpec(
            name="get_income_and_savings",
            description=(
                "Income, total spending, net position, and each category's share of income. "
                "Use for 'how much of my income goes to X' and savings-rate questions. The "
                "result includes 'income_looks_complete'. When it is false, the account "
                "shows too few salary credits to be the customer's whole income picture — "
                "say that plainly and do NOT present the ratio as a real savings rate."
            ),
            input_schema=_object({"month": _MONTH}),
            handler=insights.get_income_and_savings,
        ),
        ToolSpec(
            name="forecast_month_end",
            description=(
                "Project month-end SPENDING from the run rate so far, alongside the prior "
                "months' average. Use for 'am I on track' and 'what will I have spent by "
                "month end'. This cannot produce an account balance — the dataset has no "
                "opening balance — so if the customer asked for a projected balance, say "
                "that it is not derivable before offering this."
            ),
            input_schema=_object({"month": _MONTH}),
            handler=insights.forecast_month_end,
        ),
    ]
