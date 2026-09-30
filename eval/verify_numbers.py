"""Deterministic cross-check of an eval run against the golden reference data.

    python -m eval.verify_numbers eval/out/<run-dir>

The judge in `eval/judge.py` is a model grading a model. This script is the
second, model-free opinion: it pulls every money figure out of each answer and
looks for it in the reference data the case computes directly from the dataset
(`EvalCase.truth()`). A figure that appears in the reference is MATCHED; one that
does not is UNVERIFIED (the reference may simply not isolate that slice; the
judge decides whether it is contradicted). The match rate is the number a human
can recompute by hand, which is what "cross-verified against golden data" means.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from eval.dataset import build_cases  # noqa: E402
from eval.models import EvalRun  # noqa: E402
from penny.composition.container import container  # noqa: E402

MONEY = re.compile(
    r"(?<![\w.])\$?\s?(-?\d{1,3}(?:,\d{3})+(?:\.\d{1,2})?|-?\d+(?:\.\d{1,2})?)(?![\w.])"
)


def figures_in(text: str) -> list[float]:
    """Money-looking numbers in an answer. Years and months are skipped."""
    out: list[float] = []
    for match in MONEY.finditer(text):
        raw = match.group(1).replace(",", "")
        try:
            value = float(raw)
        except ValueError:
            continue
        if raw.isdigit() and 1900 <= int(raw) <= 2100:
            continue  # a year, not a figure
        if raw.isdigit() and int(raw) <= 12 and "." not in raw:
            continue  # a month number or a count too small to be a figure
        out.append(value)
    return out


def numbers_in(reference: Any) -> set[float]:
    """Every numeric value anywhere in the reference structure."""
    found: set[float] = set()
    if isinstance(reference, bool):
        return found
    if isinstance(reference, int | float):
        found.add(float(reference))
    elif isinstance(reference, dict):
        for value in reference.values():
            found |= numbers_in(value)
    elif isinstance(reference, list | tuple):
        for value in reference:
            found |= numbers_in(value)
    return found


def matches(figure: float, reference: set[float]) -> bool:
    """Cent-exact, whole-dollar rounded, or a percentage the reference states."""
    for value in reference:
        if abs(abs(value) - abs(figure)) <= 0.011:
            return True
        if abs(round(value) - figure) <= 0.5 and figure == round(figure):
            return True
    return False


def main() -> int:
    parser = argparse.ArgumentParser(description="Cross-check eval answers against golden data.")
    parser.add_argument("run_dir", type=Path)
    args = parser.parse_args()

    run = EvalRun.model_validate_json((args.run_dir / "run.json").read_text(encoding="utf-8"))
    cases = {c.case_id: c for c in build_cases(container.insights())}

    total_figures = matched_figures = 0
    rows: list[dict[str, Any]] = []
    for result in run.results:
        sim = result.simulation
        case = cases.get(sim.case_id)
        if case is None:
            continue
        reference = numbers_in(case.truth())
        figures = figures_in(sim.answer)
        hit = [f for f in figures if matches(f, reference)]
        miss = [f for f in figures if not matches(f, reference)]
        total_figures += len(figures)
        matched_figures += len(hit)
        rows.append(
            {
                "case": sim.case_id,
                "refusal_expected": case.expects_refusal,
                "figures": len(figures),
                "matched": len(hit),
                "unverified": miss,
                "tool_calls": sim.tool_calls,
                "error": sim.error,
                "judge_accuracy": result.verdict.accuracy if result.verdict else None,
            }
        )

    print(f"Run: agent={run.agent_model} judge={run.judge_model} cases={len(rows)}")
    print(f"{'case':22s} {'fig':>3s} {'ok':>3s}  {'tools':>5s}  judge  unverified")
    for r in rows:
        flag = " (refusal case)" if r["refusal_expected"] else ""
        judge = "-" if r["judge_accuracy"] is None else str(r["judge_accuracy"])
        err = f"  ERROR {r['error']}" if r["error"] else ""
        print(
            f"{r['case']:22s} {r['figures']:3d} {r['matched']:3d}  {r['tool_calls']:5d}  "
            f"{judge:5s}  {r['unverified']}{flag}{err}"
        )
    rate = (matched_figures / total_figures * 100) if total_figures else 0.0
    print(
        f"\nGolden match rate: {matched_figures}/{total_figures} figures "
        f"found in the reference data ({rate:.1f}%)"
    )
    print("Unverified means not present in the reference slice; see the judge for contradicted.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
