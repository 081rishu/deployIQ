"""Composite Readiness Score — spec 9.4.

Context only. Computed exclusively when all three scores are computable: a
composite built from a partial set would imply a completeness the assessment
does not have.

This is explicitly NOT a decision mechanism. Nothing in the system may
threshold this value into a verdict.
"""

from __future__ import annotations

from calc.models import BoundsType, Score, SubScore, band_for
from calc.scoring_calibration import COMPOSITE, SCORING_CALIBRATION_VERSION
from schemas.assessment_state import Provenance, RangeEstimate

# S8: product-design weighting, centralised and versioned — never presented as
# an empirical truth.
W_ECONOMIC = COMPOSITE["weight_economic"].value
W_FEASIBILITY = COMPOSITE["weight_feasibility"].value
W_RISK = COMPOSITE["weight_risk"].value


def composite_score(economic: Score, feasibility: Score, risk: Score) -> Score:
    parts = [(economic, W_ECONOMIC), (feasibility, W_FEASIBILITY), (risk, W_RISK)]
    missing = [s.label for s, _ in parts if not s.computable]
    if missing:
        return Score.not_computable(
            "composite", "Composite Readiness Score",
            [f"{m} is not computable" for m in missing])

    value = sum(s.value * w for s, w in parts)
    lo = sum((s.bounds.min if s.bounds else s.value) * w for s, w in parts)
    hi = sum((s.bounds.max if s.bounds else s.value) * w for s, w in parts)

    score = Score(
        key="composite", label="Composite Readiness Score",
        # S8: whole numbers only. One decimal implied a precision the weighting
        # cannot support.
        value=float(round(value)),
        bounds=RangeEstimate(min=round(lo, 1), max=round(hi, 1), confidence="low",
                             provenance=Provenance.DERIVED,
                             source="weighted mean of the three scores at their bounds"),
        band=band_for(value),
        bounds_type=BoundsType.SCENARIO_ENVELOPE,
        inputs_varied=[s.label for s, _ in parts],
        inputs_held_fixed=[f"composite weights (calibration v"
                           f"{SCORING_CALIBRATION_VERSION})"],
        calibration_version=SCORING_CALIBRATION_VERSION,
        sub_scores=[SubScore(key=s.key, label=s.label, value=s.value, weight=w,
                             basis=f"{s.value:.1f} ({s.band})") for s, w in parts],
        note=("Summary indicator based on the configured scoring weights "
              f"(calibration v{SCORING_CALIBRATION_VERSION}). This is NOT an "
              "overall decision score and does not decide anything; Decision "
              "Drivers are the output that matters."),
    )
    # A compliance blocker must not be diluted by averaging.
    for s, _ in parts:
        for flag in s.flags:
            if "BLOCKER" in flag:
                score.flags.append(flag)
    return score
