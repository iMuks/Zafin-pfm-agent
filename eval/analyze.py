"""Phase 3 - separate judge error from real defects.

Only low scores are re-read, by a stronger model. Every low score comes back
classified:

    FALSE_POSITIVE - the judge was wrong; the answer was fine. Carries a
                     corrected score, which feeds the FP-adjusted aggregate.
    GAP            - the answer really was deficient. This is the actionable
                     list.

Without this pass an aggregate score is not actionable, because a mediocre
number could equally mean "the agent is wrong" or "the grader is noisy". It is
also cheap: it runs on failures only.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

from langchain_core.messages import HumanMessage, SystemMessage

from eval.dataset import EvalCase
from eval.models import DIMENSIONS, LOW_SCORE_THRESHOLD, Classification, Simulation, Verdict
from penny.infrastructure.llm.model_factory import chat_model

SYSTEM = """You audit a grader's low scores for a banking assistant.

You receive the question, REFERENCE DATA computed from the dataset, the \
assistant's ANSWER, the grader's score for ONE dimension, and its rationale.

Decide which is true:

FALSE_POSITIVE - the grader was wrong. The answer was actually fine on this \
dimension. Common causes: the figure was correct but phrased differently or \
rounded; the grader missed that the reference data supports the claim; the \
grader penalised an appropriate refusal for a question the data cannot answer; \
the grader wanted detail the question did not ask for. Give the score the answer \
deserved.

GAP - the grader was right. The answer really was inaccurate, unhelpful, or \
risky on this dimension. Say specifically what is wrong, in one sentence a \
developer could act on.

A grader mistake worth catching specifically: penalising an answer for figures \
the reference does not COVER. The reference is one slice of the dataset. If the \
question implied a different period or a subset the agent had to identify \
itself — `grader_notes` will say so — then figures outside the reference are \
unverifiable, not invented, and marking them down is a FALSE_POSITIVE.

Be conservative in the other direction too: call FALSE_POSITIVE only when the \
reference supports the answer, or when the reference plainly does not cover the \
scope the question asked for. A figure the reference CONTRADICTS is always a \
GAP."""


def _prompt(
    case: EvalCase, simulation: Simulation, verdict: Verdict, dimension: str, truth: dict[str, Any]
) -> list[Any]:
    payload = {
        "dimension_under_audit": dimension,
        "grader_score": getattr(verdict, dimension),
        "grader_rationale": verdict.rationale,
        "question": case.question,
        "question_cannot_be_answered_from_transaction_history": case.expects_refusal,
        "reference_data": truth,
        "assistant_answer": simulation.answer or "(the assistant produced no text)",
    }
    return [
        SystemMessage(content=SYSTEM),
        HumanMessage(content=json.dumps(payload, indent=2, default=str)),
    ]


async def analyze(
    cases: list[EvalCase],
    simulations: list[Simulation],
    verdicts: dict[str, Verdict | str],
    concurrency: int = 3,
) -> dict[str, dict[str, Classification]]:
    """Return case_id -> {dimension: Classification} for low scores only."""
    model = chat_model("analyst", thinking=False).with_structured_output(Classification)
    by_id = {s.case_id: s for s in simulations}
    by_case = {c.case_id: c for c in cases}
    gate = asyncio.Semaphore(concurrency)

    targets: list[tuple[str, str]] = [
        (case_id, dimension)
        for case_id, verdict in verdicts.items()
        if isinstance(verdict, Verdict)
        for dimension in DIMENSIONS
        if getattr(verdict, dimension) <= LOW_SCORE_THRESHOLD
    ]

    async def audit(case_id: str, dimension: str) -> tuple[str, str, Classification | None]:
        case, simulation, verdict = by_case.get(case_id), by_id.get(case_id), verdicts.get(case_id)
        if not case or not simulation or not isinstance(verdict, Verdict):
            return case_id, dimension, None
        async with gate:
            try:
                result = await model.ainvoke(
                    _prompt(case, simulation, verdict, dimension, case.truth())
                )
                return case_id, dimension, result
            except Exception as exc:
                # An audit that fails leaves the raw score standing - never
                # silently upgrades it.
                return (
                    case_id,
                    dimension,
                    Classification(
                        verdict="GAP",
                        reason=f"analyst unavailable ({type(exc).__name__}); raw score kept",
                    ),
                )

    out: dict[str, dict[str, Classification]] = {}
    for case_id, dimension, classification in await asyncio.gather(
        *(audit(c, d) for c, d in targets)
    ):
        if classification is not None:
            out.setdefault(case_id, {})[dimension] = classification
    return out
