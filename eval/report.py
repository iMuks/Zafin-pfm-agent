"""Standalone HTML report for one eval run.

Self-contained: inline CSS, no external requests, safe to open from disk or
attach to a PR. Raw and FP-adjusted scores are shown side by side, because the
gap between them is itself a signal - a large one means the grader is noisy and
the rubric needs work, not the agent.
"""

from __future__ import annotations

import html
from pathlib import Path

from eval.models import DIMENSIONS, EvalRun

_CSS = """
*{box-sizing:border-box;margin:0;padding:0}
body{font:14px/1.55 -apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;
 color:#0d1220;background:#f4f5fa;padding:40px 28px}
.wrap{max-width:1080px;margin:0 auto}
h1{font-size:26px;letter-spacing:-.02em;margin-bottom:6px}
.sub{color:#6b7392;font-size:13px;margin-bottom:28px}
.meta{display:flex;flex-wrap:wrap;gap:8px;margin-bottom:28px}
.pill{background:#fff;border:1px solid #e7e9f2;border-radius:999px;padding:5px 12px;font-size:12px;color:#47506b}
.pill b{color:#0d1220;font-weight:600}
.cards{display:grid;grid-template-columns:repeat(auto-fit,minmax(200px,1fr));gap:12px;margin-bottom:14px}
.card{background:#fff;border:1px solid #e7e9f2;border-radius:14px;padding:16px 18px}
.card h3{font-size:10.5px;text-transform:uppercase;letter-spacing:.08em;color:#8b93ab;margin-bottom:10px}
.score{font-size:30px;font-weight:700;letter-spacing:-.02em;font-variant-numeric:tabular-nums}
.score span{font-size:14px;color:#8b93ab;font-weight:500}
.delta{font-size:12px;color:#6b7392;margin-top:4px}
.good{color:#0d9488}.warn{color:#b45309}.bad{color:#b91c1c}
h2{font-size:15px;margin:32px 0 12px;padding-bottom:8px;border-bottom:2px solid #e7e9f2}
table{width:100%;border-collapse:collapse;background:#fff;border:1px solid #e7e9f2;border-radius:12px;overflow:hidden}
th{text-align:left;font-size:10.5px;text-transform:uppercase;letter-spacing:.05em;color:#8b93ab;
 padding:10px 12px;border-bottom:1px solid #e7e9f2;background:#fafbfe}
td{padding:11px 12px;border-bottom:1px solid #f2f3f8;vertical-align:top;font-size:13px}
tr:last-child td{border-bottom:0}
td.n{font-variant-numeric:tabular-nums;text-align:center;font-weight:600}
.q{font-weight:600}
.ans{color:#47506b;font-size:12.5px;max-width:420px}
.tag{display:inline-block;font-size:10.5px;padding:2px 7px;border-radius:6px;background:#eef0ff;color:#312e81;margin-right:4px}
.tag.fp{background:#ecfdf5;color:#065f46}.tag.gap{background:#fef2f2;color:#991b1b}
.empty{color:#8b93ab;font-style:italic;padding:14px 12px}
"""


def _cls(value: float) -> str:
    return "good" if value >= 4 else "warn" if value >= 3 else "bad"


def render(run: EvalRun) -> str:
    s = run.summary()
    e = html.escape

    cards = []
    for dimension in DIMENSIONS:
        raw, adjusted = s["raw"][dimension], s["fp_adjusted"][dimension]
        moved = "" if abs(adjusted - raw) < 0.005 else f"raw {raw:.2f} → adjusted {adjusted:.2f}"
        cards.append(
            f'<div class="card"><h3>{dimension} (FP-adjusted)</h3>'
            f'<div class="score {_cls(adjusted)}">{adjusted:.2f}<span> / 5</span></div>'
            f'<div class="delta">{moved or f"raw {raw:.2f}, unchanged"}</div></div>'
        )
    cards.append(
        f'<div class="card"><h3>Grader audit</h3>'
        f'<div class="score">{s["false_positives"]}<span> FP</span></div>'
        f'<div class="delta">{s["gaps"]} real gaps · {s["scored"]}/{s["cases"]} cases scored</div></div>'
    )
    cards.append(
        f'<div class="card"><h3>Safety flags</h3>'
        f'<div class="score {"bad" if s["invented_data"] or s["claimed_action"] else "good"}">'
        f"{s['invented_data'] + s['claimed_action']}</div>"
        f'<div class="delta">{s["invented_data"]} invented data · {s["claimed_action"]} claimed an action</div></div>'
    )

    gap_rows = (
        "".join(
            f"<tr><td class='q'>{e(r.simulation.case_id)}</td><td>{e(d)}</td>"
            f"<td>{e(r.adjustments[d].reason)}</td></tr>"
            for r in run.results
            for d in r.gaps()
        )
        or '<tr><td colspan="3" class="empty">No gaps — every low score was a grader false positive.</td></tr>'
    )

    case_rows = []
    for r in run.results:
        adjusted = r.adjusted()
        tags = "".join(
            f'<span class="tag {"fp" if c.verdict == "FALSE_POSITIVE" else "gap"}">{e(d)}: '
            f"{'FP' if c.verdict == 'FALSE_POSITIVE' else 'GAP'}</span>"
            for d, c in r.adjustments.items()
        )
        answer = r.simulation.answer or r.simulation.error or "(no answer)"
        case_rows.append(
            f"<tr><td class='q'>{e(r.simulation.question)}<br>{tags}</td>"
            + "".join(f"<td class='n {_cls(adjusted[d])}'>{adjusted[d]}</td>" for d in DIMENSIONS)
            + f"<td class='n'>{r.simulation.tool_calls}</td>"
            f"<td class='ans'>{e(answer[:260])}</td></tr>"
        )

    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<title>Penny — eval run {e(run.started_at)}</title><style>{_CSS}</style></head>
<body><div class="wrap">
<h1>Penny — evaluation report</h1>
<p class="sub">Simulation → Evaluation → Analysis. Every reference figure is computed
from the same analytics functions the agent calls, so accuracy means "did it report
what the data actually says".</p>
<div class="meta">
  <span class="pill">run <b>{e(run.started_at)}</b></span>
  <span class="pill">agent <b>{e(run.agent_model)}</b></span>
  <span class="pill">judge <b>{e(run.judge_model)}</b></span>
  <span class="pill">analyst <b>{e(run.analyst_model)}</b></span>
  <span class="pill">prompt <b>{e(run.prompt_version)}</b></span>
</div>
<div class="cards">{"".join(cards)}</div>
<h2>Actionable gaps</h2>
<table><thead><tr><th>Case</th><th>Dimension</th><th>What went wrong</th></tr></thead>
<tbody>{gap_rows}</tbody></table>
<h2>Every case</h2>
<table><thead><tr><th>Question</th><th>Acc</th><th>Help</th><th>Risk</th><th>Tools</th><th>Answer</th></tr></thead>
<tbody>{"".join(case_rows)}</tbody></table>
</div></body></html>"""


def write(run: EvalRun, path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(render(run), encoding="utf-8")
    return path
