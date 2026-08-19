"""Feasibility Score — spec 9.2.

Three weighted sub-factors:

    data readiness            0.45   user-reported category
    automation achievability  0.30   estimator range
    integration complexity    0.25   band

Data readiness carries the heaviest weight because Gartner predicts that
through 2026 organizations will abandon 60% of AI projects not supported by
AI-ready data ("Lack of AI-Ready Data Puts AI Projects at Risk", 26 Feb 2025).
That is a prediction, not a measured outcome, and it justifies the WEIGHT — it
does not supply a number for the score.

Achievability deliberately penalises range width: an automation estimate of
"40-90%" is a weaker basis for a build decision than a tight "62-68%", even
though the midpoints are similar.
"""

from __future__ import annotations

from typing import Optional

from calc.models import BoundsType, Score, SubScore, band_for, clamp, midpoint
from calc.scoring_calibration import (
    DATA_READINESS_SCORES,
    FEASIBILITY,
    INTEGRATION_SCORES as CAL_INTEGRATION,
    SCORING_CALIBRATION_VERSION,
)
from schemas.assessment_state import (
    DataReadiness,
    EffortBand,
    Provenance,
    RangeEstimate,
)
from solution.schema import SolutionEstimate

# All weights and ladders from the calibration registry (spec 27).
W_DATA = FEASIBILITY["weight_data_readiness"].value
W_ACHIEVABILITY = FEASIBILITY["weight_achievability"].value
W_INTEGRATION = FEASIBILITY["weight_integration"].value
WIDTH_PENALTY = FEASIBILITY["width_penalty"].value

READINESS_SCORES = {r: DATA_READINESS_SCORES[r.value].value for r in DataReadiness}
INTEGRATION_SCORES = {b: CAL_INTEGRATION[b.value].value for b in EffortBand}


def achievability_component(automation: RangeEstimate) -> tuple[float, str]:
    base = midpoint(automation)
    width = max(0.0, automation.max - automation.min) / 100.0
    penalty = WIDTH_PENALTY * width
    return (clamp(base - penalty),
            f"{automation.min:.0f}-{automation.max:.0f}% automation, "
            f"width penalty {penalty:.1f}")


def feasibility_score(
    solution: SolutionEstimate, data_readiness: Optional[DataReadiness],
) -> Score:
    missing = []
    if data_readiness is None:
        missing.append("data_readiness (category)")
    if solution.integration_complexity is None:
        missing.append("integration_complexity")
    if missing:
        return Score.not_computable("feasibility", "Feasibility Score", missing)

    data = READINESS_SCORES[data_readiness]
    achieve, achieve_basis = achievability_component(solution.overall_automation)
    integration = INTEGRATION_SCORES[solution.integration_complexity]
    value = W_DATA * data + W_ACHIEVABILITY * achieve + W_INTEGRATION * integration

    # Bounds: vary only the estimated term; the categories are not ranges.
    lo = W_DATA * data + W_ACHIEVABILITY * clamp(solution.overall_automation.min) + W_INTEGRATION * integration
    hi = W_DATA * data + W_ACHIEVABILITY * clamp(solution.overall_automation.max) + W_INTEGRATION * integration

    return Score(
        key="feasibility", label="Feasibility Score", value=round(value, 1),
        bounds=RangeEstimate(min=round(lo, 1), max=round(hi, 1),
                             confidence=solution.overall_automation.confidence,
                             provenance=Provenance.DERIVED,
                             source="feasibility recomputed at automation bounds"),
        band=band_for(value),
        # S4: the band varies ONLY the numeric automation range. Both
        # categorical inputs are held fixed, so this band is narrower than the
        # assessment's real uncertainty — stated rather than implied.
        bounds_type=BoundsType.NUMERIC_INPUT_ENVELOPE,
        inputs_varied=["overall_automation (numeric range)"],
        inputs_held_fixed=["data_readiness (categorical)",
                           "integration_complexity (categorical)"],
        calibration_version=SCORING_CALIBRATION_VERSION,
        sub_scores=[
            SubScore(key="data_readiness", label="Data readiness", value=data,
                     weight=W_DATA, basis=data_readiness.value,
                     provenance=Provenance.USER_PROVIDED,
                     note="heaviest weight: unsupported data is the most common "
                          "cause of abandonment (Gartner prediction, Feb 2025)"),
            SubScore(key="achievability", label="Automation achievability",
                     value=round(achieve, 1), weight=W_ACHIEVABILITY,
                     basis=achieve_basis, provenance=Provenance.ESTIMATED,
                     note="wide estimate ranges are penalised"),
            SubScore(key="integration", label="Integration complexity",
                     value=integration, weight=W_INTEGRATION,
                     basis=solution.integration_complexity.value,
                     provenance=Provenance.USER_PROVIDED,
                     note="lower complexity scores higher"),
        ],
    )
