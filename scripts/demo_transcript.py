"""Run a scripted conversation against a live Penny and record the transcript.

    python scripts/demo_transcript.py --port 8020

Writes `docs/demo-transcript.md`: every turn, every component, verbatim. Two
uses — a shot list for the demo video (you know exactly what to type and what
comes back), and evidence for the build report that the flow works end to end.

The turn order mirrors the reference video's beats: a proactive opener, the
customer tapping a suggestion, deepening detail, then a question the data
cannot answer — which is where the reference video performs an account action
and Penny instead declines and offers the nearest supported answer.

This spends real money: one live model call per turn, plus the greeting.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from pathlib import Path
from typing import Any

import httpx

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

#: Each entry is (beat, what the customer says). The beat names map onto the
#: reference video so the two can be compared side by side.
TURNS: list[tuple[str, str]] = [
    ("Customer taps the first suggestion", "I would like to start saving more. How can I do this?"),
    ("Deepening — where the money actually goes", "Where did most of my money go in June?"),
    ("Recurring costs, rendered as merchant cards", "Show me all my subscriptions"),
    ("Anomaly detection", "Are there any duplicate charges I should know about?"),
    ("Trend, month over month", "How does June compare to May?"),
    (
        "The boundary — reference video opens an account here",
        "Am I on track for my savings goal this month?",
    ),
]


def _render(component: dict[str, Any]) -> str | None:
    """One component as a transcript line."""
    name = component.get("component")
    if name == "chat-response":
        return f"> {component['body']}"
    if name == "smart-loading":
        return f"_… {component['body']}_"
    if name == "suggested-user-intents":
        return "**Suggested:** " + " · ".join(f"`{i}`" for i in component["intents"])
    if name == "transaction-list":
        rows = "\n".join(
            f"| {t['merchant']} | ${t['amount']:,.2f} | {t.get('subtitle', '')} |"
            for t in component["transactions"]
        )
        return "| Merchant | Amount | Detail |\n| --- | --- | --- |\n" + rows
    if name == "table":
        head = "| " + " | ".join(component["headers"]) + " |"
        rule = "| " + " | ".join("---" for _ in component["headers"]) + " |"
        body = "\n".join("| " + " | ".join(r) + " |" for r in component["data"])
        return f"{head}\n{rule}\n{body}"
    if name in ("ordered-list", "unordered-list"):
        marker = (lambda i: f"{i}.") if name == "ordered-list" else (lambda _: "-")
        return "\n".join(f"{marker(i)} {item}" for i, item in enumerate(component["items"], 1))
    if name in ("bar-chart", "line-chart", "pie-chart"):
        points = ", ".join(f"{d['label']}={d['values'][0]:,.2f}" for d in component["data"])
        return f"**[{name}]** {component['valueTitle']} — {points}"
    if name == "unsupported":
        return f"**[unsupported]** {component.get('reason')}"
    if name in ("feedback", "done"):
        return None
    return f"**[{name}]**"


async def _stream(
    client: httpx.AsyncClient, url: str, session_id: str | None, body: dict | None
) -> tuple[list[dict], str | None, float]:
    headers = {"content-type": "application/json"}
    if session_id:
        headers["x-session-id"] = session_id

    started = time.perf_counter()
    components: list[dict] = []
    method = "POST" if body else "GET"
    async with client.stream(method, url, headers=headers, json=body, timeout=240) as response:
        response.raise_for_status()
        session_id = response.headers.get("x-session-id", session_id)
        async for line in response.aiter_lines():
            if line.strip():
                components.append(json.loads(line))
    return components, session_id, (time.perf_counter() - started) * 1000


async def run(port: int, out: Path) -> None:
    base = f"http://127.0.0.1:{port}"
    lines: list[str] = []
    session_id: str | None = None

    async with httpx.AsyncClient() as client:
        health = (await client.get(f"{base}/api/health", timeout=30)).json()
        data = health["data"]

        lines += [
            "# Penny — end-to-end demo transcript",
            "",
            "Captured from a live run. Every figure below was produced by a real model",
            "call against the committed dataset — nothing here is written by hand.",
            "",
            f"- **Model** `{health['model']}` · effort `{health['effort']}` · prompt `{health['prompt_version']}`",
            f"- **Dataset** {data['transaction_count']} transactions, "
            f"{data['first_date']} → {data['latest_date']}",
            f"- **Last complete month** {data.get('last_complete_month')} "
            f"(partial: {data.get('partial_month')})",
            f"- **Captured** {time.strftime('%Y-%m-%d %H:%M:%SZ', time.gmtime())}",
            "",
            "---",
            "",
            "## Turn 0 — Penny opens the conversation",
            "",
            "_Unprompted, on session open. The reference video's proactive weekly summary._",
            "",
        ]

        print("→ greeting")
        components, session_id, elapsed = await _stream(
            client, f"{base}/agent/greeting", session_id, None
        )
        for component in components:
            rendered = _render(component)
            if rendered:
                lines += [rendered, ""]
        lines += [f"_{len(components)} components · {elapsed:,.0f} ms_", "", "---", ""]

        for index, (beat, question) in enumerate(TURNS, 1):
            print(f"→ turn {index}: {question}")
            lines += [f"## Turn {index} — {beat}", "", f"**Customer:** {question}", ""]
            components, session_id, elapsed = await _stream(
                client, f"{base}/agent/chat", session_id, {"input": {"content": {"body": question}}}
            )
            tools = [
                c["tool"]
                for c in components
                if c.get("component") == "smart-loading" and c.get("tool")
            ]
            for component in components:
                rendered = _render(component)
                if rendered:
                    lines += [rendered, ""]
            lines += [
                f"_{len(components)} components · {len(tools)} tool call(s)"
                + (f" ({', '.join(tools)})" if tools else "")
                + f" · {elapsed:,.0f} ms_",
                "",
                "---",
                "",
            ]

    lines += [
        "## Scope note",
        "",
        "At Turn 6 the reference video opens a savings account and sets up a recurring",
        "transfer. Penny declines: the assignment scopes her to insights only, and there",
        "is no tool that could perform either action — the limit is structural, not a",
        "prompt instruction. She says what is missing and offers the nearest supported",
        "answer instead.",
        "",
    ]

    # File writes go to a worker thread: this coroutine must not block the loop.
    def _persist() -> None:
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text("\n".join(lines), encoding="utf-8")

    await asyncio.to_thread(_persist)
    print(f"\nWrote {out.relative_to(ROOT)} ({len(lines)} lines)")


def main() -> None:
    parser = argparse.ArgumentParser(description="Record a live demo transcript.")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--out", type=Path, default=ROOT / "docs" / "demo-transcript.md")
    args = parser.parse_args()
    asyncio.run(run(args.port, args.out))


if __name__ == "__main__":
    main()
