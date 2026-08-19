"""Overall Assessment Confidence — S9, spec 9.7.

CONSUMES the estimator's field-quality model rather than re-implementing
interviewer logic, then adds the two factors the scoring layer can see:
estimate range width and evidence coverage.

Confidence is NOT score magnitude. A score of 98 can have low confidence and a
score of 62 can have high confidence — they answer different questions.
"""

from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, Field

from calc.models import midpoint
from schemas.assessment_state import AssessmentState, Provenance, RangeEstimate
from solution import confidence as field_quality
from solution.schema import SolutionEstimate

# A range wider than this share of its own value counts as a wide estimate.
WIDE_RANGE_THRESHOLD = 0.30


class AssessmentConfidence(BaseModel):
    level: str = "medium"
    field_quality_level: str = "medium"
    field_quality_score: float = 0.0
    reasons: list[str] = Field(default_factory=list)
    blocking: list[str] = Field(default_factory=list)
    capped_reason: Optional[str] = None


def _rank(level: str) -> int:
    return {"low": 0, "medium": 1, "high": 2}[level]


def _name(rank: int) -> str:
    return {0: "low", 1: "medium", 2: "high"}[rank]


def assess(
    state: AssessmentState, solution: SolutionEstimate,
    evidence_backed: int = 0, assumed_inputs: int = 0,
) -> AssessmentConfidence:
    """Combine field quality, range width and evidence coverage (spec 9.7)."""
    fq = field_quality.assess(state)
    level_rank = _rank(fq.level)
    reasons: list[str] = []

    # Provenance mix of the economic inputs the user actually supplied.
    user_supplied = [a for a in fq.assessments
                     if a.quality >= field_quality.EvidenceQuality.DERIVED_FROM_GOOD]
    if user_supplied:
        reasons.append(f"{len(user_supplied)} of {len(fq.assessments)} consumed "
                       f"fields are user-provided, sourced or derived from them")

    # Range width on the estimator's headline quantity.
    auto = solution.overall_automation
    base = midpoint(auto)
    if base:
        width = (auto.max - auto.min) / base
        if width > WIDE_RANGE_THRESHOLD:
            level_rank = min(level_rank, 1)
            reasons.append(f"automation is an estimate spanning "
                           f"{auto.min:.0f}-{auto.max:.0f}% ({width:.0%} of its own "
                           f"value), which is wide enough to move the economics")
        else:
            reasons.append(f"automation is estimated at {auto.min:.0f}-{auto.max:.0f}%")

    # Evidence coverage.
    if evidence_backed == 0:
        level_rank = min(level_rank, 1)
        reasons.append("no economic input is benchmark-backed; current-process "
                       "quality is not independently benchmarked")
    if assumed_inputs:
        reasons.append(f"{assumed_inputs} calibration assumption(s) participate in "
                       f"the result")

    capped = None
    if fq.blocking:
        level_rank = 0
        capped = (f"a critical field is contradicted or missing "
                  f"({', '.join(fq.blocking)}), so confidence cannot exceed low")
    elif fq.floor_applied:
        level_rank = min(level_rank, 1)
        capped = fq.floor_applied

    if not fq.blocking:
        reasons.append("no critical field contradictions remain"
                       if not fq.floor_applied else fq.floor_applied)

    return AssessmentConfidence(
        level=_name(level_rank), field_quality_level=fq.level,
        field_quality_score=fq.score, reasons=reasons,
        blocking=list(fq.blocking), capped_reason=capped)
