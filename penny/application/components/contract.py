"""The component contract and its validator.

Validation is server-side and total. An object that fails is not dropped — it is
replaced by an `unsupported` placeholder carrying the reason. Dropping it
silently would hide the two failures this contract exists to catch: a model
emitting a malformed chart, and a client running an older component vocabulary
than the backend. A visible placeholder makes both obvious in a demo and in
production.
"""

from __future__ import annotations

from typing import Any, Final

#: component name -> the keys it must carry.
CONTRACT: Final[dict[str, tuple[str, ...]]] = {
    # Emitted by the model:
    "chat-response": ("body",),
    "table": ("headers", "data"),
    "ordered-list": ("items",),
    "unordered-list": ("items",),
    "line-chart": ("valueTitle", "labels", "data"),
    "bar-chart": ("valueTitle", "labels", "data"),
    "pie-chart": ("valueTitle", "labels", "data"),
    "suggested-user-intents": ("intents",),
    # Synthesised by the server, never accepted from the model:
    "transaction-list": ("transactions",),
    "smart-loading": ("body",),
    "try-again-error": ("body",),
    "feedback": (),
    "done": (),
}

CHART_COMPONENTS: Final[frozenset[str]] = frozenset({"line-chart", "bar-chart", "pie-chart"})

#: Server-owned. `transaction-list` is the important one: its rows are lifted
#: verbatim out of a tool result, so the model cannot mistype a merchant name or
#: an amount it was just handed — and cannot spend output tokens retyping 20 rows.
SERVER_COMPONENTS: Final[frozenset[str]] = frozenset(
    {"transaction-list", "smart-loading", "try-again-error", "feedback", "done"}
)

#: What the model is permitted to produce. The narrative components — is this
#: question worth a chart? — are a judgement call, and that is the model's job.
MODEL_COMPONENTS: Final[frozenset[str]] = frozenset(CONTRACT) - SERVER_COMPONENTS

_UNSUPPORTED = "unsupported"


def is_component_object(obj: object) -> bool:
    """True if this looks like a component at all.

    Guards against thinking traces and stray JSON that happen to be objects.
    """
    return isinstance(obj, dict) and isinstance(obj.get("component"), str)


def _unsupported(reason: str, raw: dict[str, Any]) -> dict[str, Any]:
    return {"component": _UNSUPPORTED, "reason": reason, "raw": raw}


def _valid_chart(obj: dict[str, Any]) -> str | None:
    labels, data = obj.get("labels"), obj.get("data")
    if not isinstance(labels, list) or not isinstance(data, list) or not data:
        return "labels must be a list and data a non-empty list"

    for point in data:
        if not isinstance(point, dict):
            return "each data point must be an object"
        values = point.get("values")
        if not isinstance(values, list) or not values:
            return "each data point needs a non-empty values list"
        if not all(isinstance(v, (int, float)) and not isinstance(v, bool) for v in values):
            return "chart values must be numbers, not strings"
        if obj["component"] == "pie-chart":
            # A pie slice is a share of one whole: exactly one value, 0-100.
            # Without this, a model that emits dollar amounts renders a pie
            # whose slices are meaningless.
            if len(values) != 1:
                return "a pie slice must carry exactly one value"
            if not 0 <= values[0] <= 100:
                return "pie values are percentages and must be between 0 and 100"
    return None


def _valid_table(obj: dict[str, Any]) -> str | None:
    headers, data = obj.get("headers"), obj.get("data")
    if not isinstance(headers, list) or not headers:
        return "headers must be a non-empty list"
    if not isinstance(data, list) or not data:
        return "data must be a non-empty list of rows"
    for row in data:
        if not isinstance(row, list) or len(row) != len(headers):
            return f"every row must have exactly {len(headers)} cells to match headers"
    return None


def _valid_items(obj: dict[str, Any], key: str) -> str | None:
    items = obj.get(key)
    if not isinstance(items, list) or not items:
        return f"{key} must be a non-empty list"
    return None


def validate(obj: dict[str, Any]) -> dict[str, Any]:
    """Return the component unchanged, or an `unsupported` placeholder saying why."""
    name = obj.get("component")

    if name not in CONTRACT:
        return _unsupported(f"unknown component '{name}'", obj)

    missing = [key for key in CONTRACT[name] if key not in obj]
    if missing:
        return _unsupported(f"{name} is missing {missing}", obj)

    if name == "table":
        if (reason := _valid_table(obj)) is not None:
            return _unsupported(f"table: {reason}", obj)

    elif name in CHART_COMPONENTS:
        if (reason := _valid_chart(obj)) is not None:
            return _unsupported(f"{name}: {reason}", obj)

    elif name in ("ordered-list", "unordered-list"):
        if (reason := _valid_items(obj, "items")) is not None:
            return _unsupported(f"{name}: {reason}", obj)

    elif name == "suggested-user-intents" and (reason := _valid_items(obj, "intents")) is not None:
        return _unsupported(f"suggested-user-intents: {reason}", obj)

    return obj
