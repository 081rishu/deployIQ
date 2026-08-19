"""Evidence-weighted confidence — N9 and N10.

N9: confidence was a penalty COUNT (0 -> high, 1-2 -> medium, 3+ -> low), so
two mild issues scored the same as one severe contradiction. It is now a
weighted mean of evidence quality across the fields the estimator actually
consumes, with a hard floor for severe contradictions.

N10: the field set is defined by what the ESTIMATOR uses, not by the
interviewer's per-sector required list. A contradicted `compliance_exposure`
or `data_readiness` now affects the result, because both feed the scope model,
the ranking and the scores.

The numeric mapping is a documented MVP calibration, not a probability.
"""

from __future__ import annotations

from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field

from schemas.assessment_state import (
    AssessmentState,
    FieldResolution,
    Provenance,
    RangeEstimate,
)


class EvidenceQuality(float, Enum):
    """Quality score per evidence category (MVP calibration)."""
    USER_PROVIDED_RESOLVED = 1.00
    SOURCED_BENCHMARK = 1.00
    DERIVED_FROM_GOOD = 0.85
    LLM_ESTIMATE_ANCHORED = 0.60
    LLM_ESTIMATE_BARE = 0.40
    ASSUMPTION = 0.25
    WEAK_ANSWER = 0.35          # stated but low-confidence / needs detail
    CONTRADICTED = 0.00
    MISSING = 0.00


# Fields the estimator consumes, with importance weights (N10). Importance is
# how much the field moves the estimate, not how hard it was to collect.
ESTIMATOR_FIELDS: dict[str, float] = {
    "monthly_volume": 1.0,              # scale band, inference cost, workload
    "avg_time_per_unit_minutes": 1.0,   # the reconciliation baseline (N4)
    "process": 0.8,                     # capability decomposition input
    "data_readiness": 0.9,              # scope model + feasibility score
    "current_tools": 0.8,               # integration complexity + effort
    "required_accuracy": 0.7,           # reliability gap
    "risk.compliance_exposure": 0.7,    # ranking, risk flag, scope
    "current_headcount": 0.5,           # cross-check on the labor baseline
    "existing_data": 0.3,               # narrative context only
}

# A contradiction here can refuse the estimate outright; elsewhere it only
# lowers confidence. "Do not make every field a blocker."
BLOCKING_FIELDS = {"monthly_volume", "avg_time_per_unit_minutes", "process",
                   "required_accuracy"}

# Confidence bands over the weighted mean.
HIGH_FROM = 0.80
MEDIUM_FROM = 0.55

# A wide LLM range costs confidence proportionally, capped.
MAX_WIDTH_PENALTY = 0.15


class FieldAssessment(BaseModel):
    field: str
    importance: float
    quality: float
    reason: str


class ConfidenceResult(BaseModel):
    level: str = "medium"
    score: float = 0.0
    assessments: list[FieldAssessment] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)
    blocking: list[str] = Field(default_factory=list)
    floor_applied: Optional[str] = None


def _quality_for(state: AssessmentState, field: str) -> tuple[float, str]:
    value = state.get_value(field)
    meta = state.field_resolution.get(field)
    tag = state.get_tag(field)

    if value is None or value == "" or value == []:
        return EvidenceQuality.MISSING.value, "not collected"
    if meta is not None:
        if meta.status == FieldResolution.CONTRADICTORY:
            return EvidenceQuality.CONTRADICTED.value, f"contradicted ({meta.reason or ''})"
        if meta.status == FieldResolution.AMBIGUOUS:
            return EvidenceQuality.CONTRADICTED.value, "answer was ambiguous"
        if meta.status in (FieldResolution.LOW_CONFIDENCE, FieldResolution.NEEDS_DETAIL):
            return EvidenceQuality.WEAK_ANSWER.value, meta.status.value
    if tag == Provenance.SOURCED:
        return EvidenceQuality.SOURCED_BENCHMARK.value, "sourced from the benchmark pack"
    if tag == Provenance.ASSUMED:
        return EvidenceQuality.ASSUMPTION.value, "assumed default"
    if tag == Provenance.DERIVED:
        return EvidenceQuality.DERIVED_FROM_GOOD.value, "derived"
    if tag == Provenance.ESTIMATED:
        return EvidenceQuality.LLM_ESTIMATE_BARE.value, "LLM estimate"
    return EvidenceQuality.USER_PROVIDED_RESOLVED.value, "user provided"


def assess(
    state: AssessmentState,
    estimate_ranges: Optional[list[RangeEstimate]] = None,
    extra_penalties: Optional[list[tuple[str, float]]] = None,
) -> ConfidenceResult:
    """Weighted-mean confidence over the fields the estimator consumes."""
    assessments, blocking = [], []
    weighted, total_weight = 0.0, 0.0

    for field, importance in ESTIMATOR_FIELDS.items():
        quality, reason = _quality_for(state, field)
        assessments.append(FieldAssessment(field=field, importance=importance,
                                           quality=quality, reason=reason))
        weighted += importance * quality
        total_weight += importance
        if quality == 0.0 and field in BLOCKING_FIELDS:
            blocking.append(f"{field} ({reason})")

    score = weighted / total_weight if total_weight else 0.0
    notes: list[str] = []

    # Range-width penalty: a very wide estimate is a weaker basis than a tight
    # one even when everything else is equal.
    if estimate_ranges:
        widths = []
        for r in estimate_ranges:
            mid = (r.min + r.max) / 2.0
            if mid > 0:
                widths.append((r.max - r.min) / mid)
        if widths:
            avg_width = sum(widths) / len(widths)
            penalty = min(MAX_WIDTH_PENALTY, avg_width * MAX_WIDTH_PENALTY)
            if penalty > 0.01:
                score -= penalty
                notes.append(f"estimate ranges average {avg_width:.0%} of their own "
                             f"value; confidence reduced by {penalty:.2f}")

    for reason, penalty in (extra_penalties or []):
        score -= penalty
        notes.append(reason)

    score = max(0.0, min(1.0, score))
    level = "high" if score >= HIGH_FROM else "medium" if score >= MEDIUM_FROM else "low"

    floor = None
    if blocking:
        floor = ("a critical field is contradicted or missing, so confidence "
                 "cannot exceed low regardless of the weighted score")
        level = "low"
    else:
        # A contradiction anywhere the estimator reads caps confidence at
        # medium: the weighted mean alone would let one flat contradiction on a
        # mid-importance field still read as "high".
        contradicted = [a.field for a in assessments
                        if a.quality == 0.0 and state.get_value(a.field) is not None]
        if contradicted and level == "high":
            level = "medium"
            floor = (f"contradicted input on {', '.join(contradicted)} caps "
                     f"confidence at medium")

    weak = [a for a in assessments if 0.0 < a.quality < EvidenceQuality.DERIVED_FROM_GOOD]
    if weak:
        notes.append("weaker inputs: " +
                     ", ".join(f"{a.field} ({a.reason})" for a in weak[:4]))
    if not notes:
        notes.append("all consumed fields resolved from user-provided or sourced "
                     "values; no assumption carries significant weight")

    return ConfidenceResult(level=level, score=round(score, 3), assessments=assessments,
                            notes=notes, blocking=blocking, floor_applied=floor)
