"""Component gallery - the real app with stub runtimes injected.

    python scripts/preview_components.py     # http://127.0.0.1:8001

Streams one of every component in the contract, including two deliberately
malformed ones to show the `unsupported` fallback, so the UI can be checked,
screenshotted and demoed without an API key and without a model call.

It swaps implementations through `penny.composition.container.override`, which is
the whole point of having Protocol seams: nothing in the production code changes
to run the app in this mode.
"""

from __future__ import annotations

import asyncio
import sys
from collections.abc import AsyncIterator
from datetime import date
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from penny.application.components.contract import is_component_object, validate  # noqa: E402
from penny.composition.container import override  # noqa: E402
from penny.domain.models import Transaction  # noqa: E402
from penny.infrastructure.persistence.json_transactions import (  # noqa: E402
    InMemoryTransactionRepository,
)

PACE_SECONDS = 0.25

GALLERY: list[dict[str, Any]] = [
    {
        "component": "smart-loading",
        "body": "Adding up category spend",
        "tool": "get_spending_by_category",
    },
    {
        "component": "chat-response",
        "body": "You spent $412.80 on dining in June - up 18% from May, and your "
        "highest month since February. Shake Shack alone accounts for $88.20 of it.",
    },
    {
        "component": "bar-chart",
        "valueTitle": "Dining spend ($)",
        "labels": ["Dining"],
        "data": [
            {"label": "Apr", "values": [318.4]},
            {"label": "May", "values": [349.1]},
            {"label": "Jun", "values": [412.8]},
        ],
    },
    {
        "component": "table",
        "headers": ["Merchant", "Visits", "Total"],
        "data": [
            ["Shake Shack", "4", "$88.20"],
            ["Chipotle", "3", "$61.05"],
            ["Starbucks", "3", "$21.40"],
        ],
    },
    {
        "component": "smart-loading",
        "body": "Scanning for recurring charges",
        "tool": "list_subscriptions",
    },
    {
        "component": "transaction-list",
        "transactions": [
            {
                "merchant": "Netflix",
                "amount": 22.99,
                "subtitle": "monthly · 4 charges",
                "logo_domain": "netflix.com",
            },
            {
                "merchant": "Spotify",
                "amount": 11.99,
                "subtitle": "monthly · 5 charges",
                "logo_domain": "spotify.com",
            },
            {
                "merchant": "Hulu",
                "amount": 17.99,
                "subtitle": "monthly · 5 charges",
                "logo_domain": "hulu.com",
            },
            {
                "merchant": "Wire Transfer Fee",
                "amount": 5.90,
                "subtitle": "2026-06-02 · Fees & Charges",
                "logo_domain": None,
            },
        ],
    },
    {
        "component": "pie-chart",
        "valueTitle": "Share of spend (%)",
        "labels": ["Share"],
        "data": [
            {"label": "Groceries", "values": [34.2]},
            {"label": "Dining", "values": [21.8]},
            {"label": "Transport", "values": [18.5]},
            {"label": "Everything else", "values": [25.5]},
        ],
    },
    {
        "component": "line-chart",
        "valueTitle": "Total spend ($)",
        "labels": ["Total"],
        "data": [
            {"label": "Feb", "values": [2210.5]},
            {"label": "Mar", "values": [1980.2]},
            {"label": "Apr", "values": [2410.9]},
            {"label": "May", "values": [2180.0]},
            {"label": "Jun", "values": [2544.6]},
        ],
    },
    {
        "component": "ordered-list",
        "items": ["Groceries - $1,204.60", "Dining - $412.80", "Transport - $388.15"],
    },
    {
        "component": "unordered-list",
        "items": ["Netflix charged twice on 12 Jun", "A $3,552 wire is 14x your usual transfer"],
    },
    # Deliberately invalid - proves graceful degradation rather than a crash.
    {"component": "sankey-diagram", "flows": []},
    {"component": "table", "headers": ["Merchant", "Total"], "data": [["Starbucks"]]},
    {
        "component": "suggested-user-intents",
        "intents": ["Break that down by merchant", "Compare to May", "Show me the transactions"],
    },
    {"component": "feedback"},
]

GREETING: list[dict[str, Any]] = [
    {"component": "smart-loading", "body": "Reading your recent spending"},
    {
        "component": "chat-response",
        "body": "Morning - June came in at $2,544.60, about 12% above May, and "
        "Groceries took $1,204.60 of it. I also spotted a Netflix charge "
        "that landed twice within three days.",
    },
    {
        "component": "bar-chart",
        "valueTitle": "Spend ($)",
        "labels": ["Total"],
        "data": [
            {"label": "Mar", "values": [1980.2]},
            {"label": "Apr", "values": [2410.9]},
            {"label": "May", "values": [2180.0]},
            {"label": "Jun", "values": [2544.6]},
        ],
    },
    {
        "component": "suggested-user-intents",
        "intents": [
            "How much did I spend on Groceries?",
            "Compare June to May",
            "Any duplicate charges?",
        ],
    },
]


async def _replay(script: list[dict[str, Any]]) -> AsyncIterator[dict[str, Any]]:
    for raw in script:
        await asyncio.sleep(PACE_SECONDS)
        yield validate(raw) if is_component_object(raw) else raw
    yield {"component": "done"}


class StubChatRuntime:
    async def stream(
        self, *, message: str, session_id: str | None
    ) -> AsyncIterator[dict[str, Any]]:
        async for component in _replay(GALLERY):
            yield component


class StubGreetingRuntime:
    async def stream(self) -> AsyncIterator[dict[str, Any]]:
        async for component in _replay(GREETING):
            yield component


def _stub_transactions() -> InMemoryTransactionRepository:
    """A handful of transactions so the data-dependent endpoints answer.

    The render path is what this previews, so the data layer is stubbed too and
    the gallery runs on a fresh clone — before enrichment has produced anything.
    """
    rows = [
        (
            "2026-05-04",
            "WHOLE FOODS MKT 4412",
            118.40,
            "Whole Foods",
            "Groceries",
            "wholefoods.com",
        ),
        ("2026-05-19", "SHAKE SHACK 0421", 24.10, "Shake Shack", "Dining", "shakeshack.com"),
        ("2026-06-02", "NETFLIX.COM", 22.99, "Netflix", "Subscriptions", "netflix.com"),
        ("2026-06-09", "WHOLE FOODS MKT 4412", 80.50, "Whole Foods", "Groceries", "wholefoods.com"),
        ("2026-06-18", "UBER *TRIP", 31.75, "Uber", "Transport", "uber.com"),
        ("2026-06-27", "SHELL OIL 5521", 54.20, "Shell", "Fuel", "shell.com"),
    ]
    return InMemoryTransactionRepository(
        [
            Transaction(
                id=f"stub_{index:03d}",
                day=date.fromisoformat(day),
                description=description,
                amount=amount,
                merchant=merchant,
                category=category,
                direction="debit",
                is_recurring=category == "Subscriptions",
                logo_domain=logo,
            )
            for index, (day, description, amount, merchant, category, logo) in enumerate(rows, 1)
        ]
    )


def main() -> None:
    import uvicorn

    from penny.presentation.http.app import create_app

    # Every collaborator the gallery needs is injected through the container,
    # so the credential check for a live runtime is never reached.
    override(
        transactions=_stub_transactions(),
        chat=StubChatRuntime(),
        greeting=StubGreetingRuntime(),
    )

    print("Component gallery on http://127.0.0.1:8001 — no API key used, no model calls made.")
    uvicorn.run(create_app(), port=8001, log_level="warning")


if __name__ == "__main__":
    main()
