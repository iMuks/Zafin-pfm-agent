"""The single façade the tool layer binds to.

Each insight family is its own class (Single Responsibility), but the tool
registry should not have to know that `detect_anomalies` lives in
`AnomalyInsights` while `compare_periods` lives in `TrendInsights`. This façade
is the seam: one object, one constructor argument, ten methods that map exactly
onto the ten tools.

It deliberately adds no logic of its own. If a method here ever grows a
calculation, that calculation belongs in an insight module instead.
"""

from __future__ import annotations

from typing import Any

from penny.application.insights.anomalies import AnomalyInsights
from penny.application.insights.coverage import CoverageInsights
from penny.application.insights.forecast import ForecastInsights
from penny.application.insights.income import IncomeInsights
from penny.application.insights.merchants import MerchantInsights
from penny.application.insights.recurring import RecurringInsights
from penny.application.insights.spending import SpendingInsights
from penny.application.insights.trends import TrendInsights
from penny.application.ports.transactions import TransactionRepository


class InsightService:
    """Every question Penny can answer, over one repository."""

    def __init__(self, repository: TransactionRepository) -> None:
        self._coverage = CoverageInsights(repository)
        self._spending = SpendingInsights(repository)
        self._merchants = MerchantInsights(repository)
        self._recurring = RecurringInsights(repository)
        self._anomalies = AnomalyInsights(repository)
        self._trends = TrendInsights(repository)
        self._income = IncomeInsights(repository)
        self._forecast = ForecastInsights(repository)

    # -- coverage ---------------------------------------------------------
    def get_data_coverage(self) -> dict[str, Any]:
        return self._coverage.data_coverage()

    # -- spending ---------------------------------------------------------
    def get_spending_by_category(
        self, category: str | None = None, month: str | None = None
    ) -> dict[str, Any]:
        return self._spending.by_category(category=category, month=month)

    def get_top_categories(self, month: str | None = None, limit: int = 3) -> dict[str, Any]:
        return self._spending.top_categories(month=month, limit=limit)

    # -- merchants --------------------------------------------------------
    def get_top_merchants(
        self, month: str | None = None, category: str | None = None, limit: int = 5
    ) -> dict[str, Any]:
        return self._merchants.top_merchants(month=month, category=category, limit=limit)

    def find_transactions(
        self,
        merchant: str | None = None,
        category: str | None = None,
        month: str | None = None,
        min_amount: float | None = None,
        limit: int = 20,
    ) -> dict[str, Any]:
        return self._merchants.find_transactions(
            merchant=merchant,
            category=category,
            month=month,
            min_amount=min_amount,
            limit=limit,
        )

    # -- recurring --------------------------------------------------------
    def list_subscriptions(self) -> dict[str, Any]:
        return self._recurring.subscriptions()

    # -- anomalies --------------------------------------------------------
    def detect_anomalies(self, month: str | None = None) -> dict[str, Any]:
        return self._anomalies.detect(month=month)

    # -- trends -----------------------------------------------------------
    def compare_periods(
        self, month_a: str, month_b: str, category: str | None = None
    ) -> dict[str, Any]:
        return self._trends.compare_periods(month_a, month_b, category=category)

    def monthly_totals(self, limit: int = 6) -> dict[str, Any]:
        return self._trends.monthly_totals(limit=limit)

    # -- income & forecast ------------------------------------------------
    def get_income_and_savings(self, month: str | None = None) -> dict[str, Any]:
        return self._income.income_and_savings(month=month)

    def forecast_month_end(self, month: str | None = None) -> dict[str, Any]:
        return self._forecast.month_end(month=month)
