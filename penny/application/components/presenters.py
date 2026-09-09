"""Server-synthesised components.

These are the components the model is *not* allowed to produce, and each is
withheld for a specific reason:

* `transaction-list` — the rows come straight from a tool result. Having the
  model retype twenty merchant names and amounts would burn output tokens and
  introduce a class of error (a transposed digit in a figure it was just given)
  that no amount of prompting reliably removes.
* `smart-loading` — the label shown while a tool runs. Deriving it from the tool
  name costs nothing and means the model spends no tokens narrating its own work.
* `try-again-error` / `feedback` / `done` — control-plane objects the model has
  no business emitting.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

#: What the customer sees while each tool runs. Phrased as Penny working, not as
#: a function executing — "Adding up category spend", not "get_spending_by_category".
TOOL_LABELS: dict[str, str] = {
    "get_data_coverage": "Checking what data I have",
    "get_spending_by_category": "Adding up category spend",
    "get_top_categories": "Ranking your categories",
    "get_top_merchants": "Ranking your merchants",
    "list_subscriptions": "Scanning for recurring charges",
    "compare_periods": "Comparing the two months",
    "detect_anomalies": "Looking for unusual charges",
    "find_transactions": "Pulling up transactions",
    "get_income_and_savings": "Weighing spend against income",
    "forecast_month_end": "Projecting the rest of the month",
}

#: A phone screen shows a handful of cards well and a long list badly. The full
#: count still reaches the model, so it can say "and 14 more".
MAX_CARDS = 8


def tool_label(name: str) -> str:
    return TOOL_LABELS.get(name, f"Running {name}")


def smart_loading(tool_name: str) -> dict[str, Any]:
    return {"component": "smart-loading", "body": tool_label(tool_name), "tool": tool_name}


def try_again_error(body: str) -> dict[str, Any]:
    return {"component": "try-again-error", "body": body}


def done(telemetry: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """Terminates the stream. Carries timings so the UI can show what it cost."""
    payload: dict[str, Any] = {"component": "done"}
    if telemetry:
        payload["telemetry"] = dict(telemetry)
    return payload


def _card(merchant: str, amount: float, subtitle: str, logo_domain: str | None) -> dict[str, Any]:
    return {
        "merchant": merchant,
        "amount": amount,
        "subtitle": subtitle,
        "logo_domain": logo_domain,
    }


def _rows(tool_name: str, result: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Map a tool result onto merchant cards, or nothing if it has no rows."""
    if tool_name == "find_transactions":
        return [
            _card(
                t["merchant"],
                t["amount"],
                " · ".join(filter(None, [t.get("date"), t.get("category"), t.get("location")])),
                t.get("logo_domain"),
            )
            for t in result.get("transactions", [])
        ]

    if tool_name == "list_subscriptions":
        return [
            _card(
                s["merchant"],
                s["typical_amount"],
                f"{s['cadence']} · {s['charge_count']} charges"
                + (" · amount varies" if s.get("amount_varies") else ""),
                s.get("logo_domain"),
            )
            for s in result.get("subscriptions", [])
        ]

    if tool_name == "get_top_merchants":
        return [
            _card(
                m["merchant"],
                m["total"],
                f"{m['visits']} visits · ${m['average_per_visit']:,.2f} avg",
                m.get("logo_domain"),
            )
            for m in result.get("merchants", [])
        ]

    return []


def transaction_list(tool_name: str, result: Any) -> dict[str, Any] | None:
    """Build a `transaction-list` from a tool result, or None if it has no rows."""
    if not isinstance(result, Mapping):
        return None
    try:
        rows = _rows(tool_name, result)
    except (KeyError, TypeError, ValueError):
        # A malformed tool result must degrade to "no cards", never break the
        # stream that is already mid-flight to the customer.
        return None
    if not rows:
        return None
    return {
        "component": "transaction-list",
        "transactions": rows[:MAX_CARDS],
        "total_count": len(rows),
    }
