"""Eval runner.

    python -m eval.run_eval                 # full set
    python -m eval.run_eval --limit 4       # smoke run
    python -m eval.run_eval --out eval/out  # where to write

Writes `run.json` (the raw record) and `report.html` (standalone) and prints a
summary. Every phase is async with bounded concurrency.

This spends real money: one agent turn, one judge call per case, and one
analyst call per low score.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import time
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

load_dotenv()

from eval import analyze as analysis  # noqa: E402
from eval import judge as judging  # noqa: E402
from eval import report as reporting  # noqa: E402
from eval import simulate as simulation  # noqa: E402
from eval.dataset import build_cases  # noqa: E402
from eval.models import CaseResult, EvalRun, Verdict  # noqa: E402
from penny.composition.container import container  # noqa: E402
from penny.infrastructure.config.models import resolve_model  # noqa: E402
from penny.infrastructure.observability.telemetry import setup  # noqa: E402


def _checkpoint(out_dir: Path, name: str, payload: Any) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / name).write_text(
        json.dumps(payload, indent=2, default=lambda o: o.model_dump()), encoding="utf-8"
    )


async def run(limit: int | None, concurrency: int, out_dir: Path) -> EvalRun:
    setup()
    # Ground truth comes from the same insight service the agent's tools call.
    cases = build_cases(container.insights())
    if limit:
        cases = cases[:limit]

    print(f"[1/3] simulation  — {len(cases)} cases against {resolve_model('chat').model_id}")
    simulations = await simulation.simulate(cases, concurrency)
    # Checkpoint each phase: a judge or analyst stall must never cost the
    # simulations already paid for. `simulations.json` is also enough for the
    # model-free cross-check in eval/verify_numbers.py.
    await asyncio.to_thread(_checkpoint, out_dir, "simulations.json", simulations)
    failed = [s.case_id for s in simulations if s.error]
    if failed:
        print(f"      {len(failed)} case(s) errored: {', '.join(failed)}")

    print(f"[2/3] evaluation  — judging with {resolve_model('judge').model_id}")
    verdicts = await judging.judge(cases, simulations, concurrency)
    await asyncio.to_thread(
        _checkpoint,
        out_dir,
        "verdicts.json",
        {k: (v.model_dump() if isinstance(v, Verdict) else v) for k, v in verdicts.items()},
    )

    low = sum(
        1
        for v in verdicts.values()
        if isinstance(v, Verdict)
        for d in ("accuracy", "helpfulness", "risk")
        if getattr(v, d) <= 3
    )
    print(
        f"[3/3] analysis    — auditing {low} low score(s) with {resolve_model('analyst').model_id}"
    )
    adjustments = await analysis.analyze(cases, simulations, verdicts, concurrency)

    results = []
    for sim in simulations:
        verdict = verdicts.get(sim.case_id)
        results.append(
            CaseResult(
                simulation=sim,
                verdict=verdict if isinstance(verdict, Verdict) else None,
                judge_error=verdict if isinstance(verdict, str) else None,
                adjustments=adjustments.get(sim.case_id, {}),
            )
        )

    run_record = EvalRun(
        started_at=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        agent_model=resolve_model("chat").model_id,
        judge_model=resolve_model("judge").model_id,
        analyst_model=resolve_model("analyst").model_id,
        prompt_version=container.prompts().version,
        results=results,
    )

    # File writes go to a worker thread: this coroutine must not block the loop.
    def _persist() -> None:
        out_dir.mkdir(parents=True, exist_ok=True)
        (out_dir / "run.json").write_text(run_record.model_dump_json(indent=2), encoding="utf-8")
        reporting.write(run_record, out_dir / "report.html")

    await asyncio.to_thread(_persist)

    s = run_record.summary()
    print("\n— summary —")
    for dimension in ("accuracy", "helpfulness", "risk"):
        print(
            f"  {dimension:12s} raw {s['raw'][dimension]:.2f}   fp-adjusted {s['fp_adjusted'][dimension]:.2f}"
        )
    print(f"  false positives {s['false_positives']}   real gaps {s['gaps']}")
    print(f"  invented data {s['invented_data']}   claimed action {s['claimed_action']}")
    print(f"\n  {out_dir / 'report.html'}")
    return run_record


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the Penny eval suite.")
    parser.add_argument("--limit", type=int, default=None, help="Only the first N cases.")
    parser.add_argument("--concurrency", type=int, default=3)
    parser.add_argument("--out", type=Path, default=Path("eval/out"))
    args = parser.parse_args()
    asyncio.run(run(args.limit, args.concurrency, args.out))


if __name__ == "__main__":
    main()
