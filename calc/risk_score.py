"""Risk Score — spec 9.3. Higher = safer.

    raw_risk   = failure_probability x impact_weight
    raw_risk  += reliability-gap penalty (only when the gap is positive)
    Risk Score = 100 x (1 - min(raw_risk, 1))

Probability x impact is the conventional formulation (PMBOK / ISO 31000
lineage); it needs no vendor citation.

COMPLIANCE IS NOT A NUMERIC TERM. Where constraints exist and the selected
solution does not cover them, the score is forced to 0 and a blocker flag is
raised. The flag surfaces regardless of the numeric score — strong economics
must never average a compliance blocker away.
"""

from __future__ import annotations

from typing import Optional

from calc.models import BoundsType, Score, SubScore, band_for, clamp, midpoint
from calc.scoring_calibration import (
    IMPACT_SEVERITY_WEIGHTS,
    SCORING_CALIBRATION_VERSION,
    escape_fraction,
    reliability_modifier,
)
from schemas.assessment_state import (
    AssessmentState,
    ImpactSeverity,
    Provenance,
    RangeEstimate,
)
from solution.schema import SolutionEstimate

# S6: the severity ladder is a calibration, centralised and versioned. There is
# exactly one numeric ladder in the system.
IMPACT_WEIGHTS = {sev: IMPACT_SEVERITY_WEIGHTS[sev.value].value
                  for sev in ImpactSeverity}

# Performance metrics that express failure likelihood directly.
_FAILURE_METRICS = ("hallucination_rate", "exception_rate", "error_rate")
_ACCURACY_METRICS = ("extraction_accuracy", "answer_accuracy", "resolution_rate")


def derive_failure_probability(solution: SolutionEstimate) -> Optional[RangeEstimate]:
    """Failure probability from the selected architecture's own metrics.

    Prefers a metric that measures failure directly; otherwise inverts an
    accuracy metric. Tagged DERIVED, carrying the originating citation.
    """
    for pm in solution.performance:
        if pm.metric in _FAILURE_METRICS:
            e = pm.estimate
            return RangeEstimate(min=e.min / 100.0, max=e.max / 100.0,
                                 confidence=e.confidence, provenance=Provenance.DERIVED,
                                 source=f"from {pm.metric}: {e.source}")
    for pm in solution.performance:
        if pm.metric in _ACCURACY_METRICS:
            e = pm.estimate
            return RangeEstimate(min=(100.0 - e.max) / 100.0, max=(100.0 - e.min) / 100.0,
                                 confidence=e.confidence, provenance=Provenance.DERIVED,
                                 source=f"1 - {pm.metric}: {e.source}")
    return None


def reliability_gap(
    required: Optional[RangeEstimate], achievable: Optional[RangeEstimate],
) -> Optional[float]:
    """Required accuracy minus expected achievable accuracy; None if unknown,
    0 when the solution clears the bar."""
    if required is None or achievable is None:
        return None
    return max(0.0, midpoint(required) - midpoint(achievable))


def compliance_blocked(state: AssessmentState, solution: SolutionEstimate) -> Optional[str]:
    constraints = list(state.risk.compliance_exposure or [])
    if not constraints:
        return None
    gaps = [r for r in solution.risks_and_mitigations
            if "compliance" in str(r.get("risk", "")).lower()]
    if gaps:
        return (f"COMPLIANCE BLOCKER: {', '.join(constraints)} declared, and the "
                f"selected solution does not cover it ({gaps[0].get('risk')}). "
                f"This is a hard flag, not a score adjustment.")
    return None


def dominant_hitl(solution: SolutionEstimate) -> str:
    """The HITL mode most of the workload passes through."""
    if not solution.task_automation:
        return "autonomous"
    by_mode: dict[str, float] = {}
    for t in solution.task_automation:
        by_mode[t.hitl.value] = by_mode.get(t.hitl.value, 0.0) + max(t.workload_share, 0.0)
    return max(by_mode, key=lambda k: by_mode[k])


def residual_failure_probability(
    raw_error: RangeEstimate, hitl_mode: str,
) -> tuple[RangeEstimate, str]:
    """S7: model error is not business failure — review catches some of it.

    residual = raw_error x escape_fraction(HITL mode)

    The escape fraction is an explicit assumption RANGE per mode, not a
    universal "human review catches 90%". With no review, residual == raw.
    """
    lo_esc, hi_esc, why = escape_fraction(hitl_mode)
    residual = RangeEstimate(
        min=raw_error.min * lo_esc, max=raw_error.max * hi_esc,
        confidence="low", provenance=Provenance.DERIVED,
        source=(f"raw error {raw_error.min:.1%}-{raw_error.max:.1%} x escape "
                f"fraction {lo_esc:.0%}-{hi_esc:.0%} for HITL mode "
                f"'{hitl_mode}' [risk.escape_fraction.{hitl_mode}]"))
    statement = (f"residual failure after '{hitl_mode}' handling: "
                 f"{residual.min:.1%}-{residual.max:.1%} of output, from a raw "
                 f"error rate of {raw_error.min:.1%}-{raw_error.max:.1%}. "
                 f"Escape fraction is an MVP assumption: {why}")
    return residual, statement


def risk_score(
    state: AssessmentState, solution: SolutionEstimate,
    failure_probability: Optional[RangeEstimate] = None,
    required_accuracy: Optional[RangeEstimate] = None,
    achievable_accuracy: Optional[RangeEstimate] = None,
) -> Score:
    # DERIVED ONLY: there is no collected failure probability to prefer. The
    # optional argument exists for sensitivity sweeps, not for user input.
    raw = failure_probability or derive_failure_probability(solution)
    severity = state.risk.failure_impact_severity
    hitl_mode = dominant_hitl(solution)
    prob, residual_note = (residual_failure_probability(raw, hitl_mode)
                           if raw is not None else (None, ""))

    missing = []
    if prob is None:
        missing.append("failure_probability (no estimate and no architecture metric)")
    if severity is None:
        missing.append("failure_impact_severity (category)")
    if missing:
        score = Score.not_computable("risk", "Risk Score", missing)
        blocker = compliance_blocked(state, solution)
        if blocker:
            # A blocker must surface even when the numeric score cannot.
            score.flags.append(blocker)
        return score

    impact_w = IMPACT_WEIGHTS[severity]
    gap = reliability_gap(required_accuracy, achievable_accuracy)
    # S5: the reliability gap MODIFIES base risk; it does not replace failure
    # probability and is not an invented additive penalty.
    modifier, modifier_param = reliability_modifier(gap)

    def raw_at(p: float) -> float:
        return min(p * impact_w * modifier, 1.0)

    value = 100.0 * (1.0 - raw_at(midpoint(prob)))
    best = 100.0 * (1.0 - raw_at(prob.min))
    worst = 100.0 * (1.0 - raw_at(prob.max))

    flags = []
    blocker = compliance_blocked(state, solution)
    if blocker:
        flags.append(blocker)
        value = 0.0
        best = worst = 0.0
    if gap:
        flags.append(f"reliability gap: expected accuracy falls {gap:.1%} short "
                     f"of the required bar")

    subs = [
        SubScore(key="raw_error", label="Raw model error",
                 value=round(100.0 * midpoint(raw), 1), weight=0.0,
                 basis=f"{raw.min:.1%}-{raw.max:.1%}", provenance=raw.provenance,
                 note="before human review; NOT the business failure rate"),
        SubScore(key="failure_probability", label="Residual failure (after HITL)",
                 value=round(100.0 * midpoint(prob), 1), weight=1.0,
                 basis=f"{prob.min:.1%}-{prob.max:.1%}", provenance=prob.provenance,
                 note=residual_note),
        SubScore(key="failure_impact", label="Failure impact", value=100.0 * impact_w,
                 weight=1.0, basis=severity.value, provenance=Provenance.USER_PROVIDED,
                 note="consequence severity, user-reported"),
    ]
    if gap is not None:
        subs.append(SubScore(
            key="reliability_gap", label="Reliability gap", value=round(100.0 * gap, 1),
            weight=modifier, basis=f"{gap:.1%} shortfall",
            note=(f"modifier x{modifier} [{modifier_param.parameter_id}]: "
                  f"{modifier_param.rationale}")))

    return Score(
        key="risk", label="Risk Score (higher = safer)", value=round(value, 1),
        bounds=RangeEstimate(min=round(min(best, worst), 1), max=round(max(best, worst), 1),
                             confidence=prob.confidence, provenance=Provenance.DERIVED,
                             source="risk recomputed at failure-probability bounds"),
        band=band_for(value), sub_scores=subs, flags=flags,
        bounds_type=BoundsType.NUMERIC_INPUT_ENVELOPE,
        inputs_varied=["failure_probability"],
        inputs_held_fixed=["failure_impact_severity (categorical)",
                           "reliability modifier band (categorical)"],
        calibration_version=SCORING_CALIBRATION_VERSION,
        note=("compliance is a hard flag, not a numeric term — it forces the "
              "score to zero rather than being averaged in"),
    )
