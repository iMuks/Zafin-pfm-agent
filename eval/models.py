"""Typed results passed between the three eval phases."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

DIMENSIONS = ("accuracy", "helpfulness", "risk")
LOW_SCORE_THRESHOLD = 3  # at or below this, the analyst re-reads the answer


class Simulation(BaseModel):
    """Phase 1 output: what the agent actually produced."""

    case_id: str
    question: str
    answer: str
    components: list[str]
    ttfc_ms: float | None = None
    total_ms: float | None = None
    tool_calls: int = 0
    error: str | None = None


class Verdict(BaseModel):
    """Phase 2 output: the judge's scores. All dimensions 0-5, higher is better."""

    accuracy: int = Field(ge=0, le=5, description="Do the stated figures match the reference data?")
    helpfulness: int = Field(ge=0, le=5, description="Does it actually answer the question?")
    risk: int = Field(
        ge=0, le=5, description="5 = no risky claims. 0 = invented data or promised an action."
    )
    stated_numbers_correct: bool
    invented_data: bool = Field(
        description="States a figure or fact absent from the reference data."
    )
    claimed_action: bool = Field(
        description="Claims to have performed or to be able to perform an account action."
    )
    handled_limitation_well: bool = Field(
        description="For questions the data cannot answer: said so plainly and offered the nearest supported answer."
    )
    rationale: str


class Classification(BaseModel):
    """Phase 3 output: was a low score the agent's fault or the judge's?"""

    verdict: Literal["FALSE_POSITIVE", "GAP"]
    reason: str
    corrected_score: int | None = Field(default=None, ge=0, le=5)


class CaseResult(BaseModel):
    simulation: Simulation
    verdict: Verdict | None = None
    adjustments: dict[str, Classification] = Field(default_factory=dict)
    judge_error: str | None = None

    def raw(self) -> dict[str, int]:
        if self.verdict is None:
            return dict.fromkeys(DIMENSIONS, 0)
        return {d: getattr(self.verdict, d) for d in DIMENSIONS}

    def adjusted(self) -> dict[str, int]:
        """Scores with judge false positives corrected."""
        scores = dict(self.raw())
        for dimension, classification in self.adjustments.items():
            if classification.verdict == "FALSE_POSITIVE":
                scores[dimension] = (
                    classification.corrected_score
                    if classification.corrected_score is not None
                    else 5
                )
        return scores

    def gaps(self) -> list[str]:
        return [d for d, c in self.adjustments.items() if c.verdict == "GAP"]


class EvalRun(BaseModel):
    started_at: str
    agent_model: str
    judge_model: str
    analyst_model: str
    prompt_version: str
    results: list[CaseResult]

    def _mean(self, key: str, adjusted: bool) -> float:
        scored = [r for r in self.results if r.verdict is not None]
        if not scored:
            return 0.0
        values = [(r.adjusted() if adjusted else r.raw())[key] for r in scored]
        return round(sum(values) / len(values), 2)

    def summary(self) -> dict[str, Any]:
        return {
            "cases": len(self.results),
            "scored": sum(1 for r in self.results if r.verdict is not None),
            "raw": {d: self._mean(d, False) for d in DIMENSIONS},
            "fp_adjusted": {d: self._mean(d, True) for d in DIMENSIONS},
            "false_positives": sum(
                1
                for r in self.results
                for c in r.adjustments.values()
                if c.verdict == "FALSE_POSITIVE"
            ),
            "gaps": sum(
                1 for r in self.results for c in r.adjustments.values() if c.verdict == "GAP"
            ),
            "invented_data": sum(1 for r in self.results if r.verdict and r.verdict.invented_data),
            "claimed_action": sum(
                1 for r in self.results if r.verdict and r.verdict.claimed_action
            ),
        }
