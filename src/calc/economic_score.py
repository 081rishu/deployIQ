"""Economic Score — spec 9.1.

Grounded in the labor baseline (8.1), never in benchmark figures: benchmarks
cross-check the baseline (8.8), they do not feed the score.

Two components, blended. Spec 9.1 does not fix the blend, so it is named here
rather than buried:

    payback        weight 0.60   how fast the cost is recouped
    benefit/cost   weight 0.40   how much is returned per unit spent

A missing payback (spec 8.7: no positive net benefit) scores 0 on that
component — a real result, not an error.
"""

from __future__ import annotations

from typing import Optional

from calc import economic_sanity as sanity_mod
from calc.lifecycle import FirstYearEconomics
from calc.models import BoundsType, Score, SubScore, band_for, clamp, midpoint, money
from calc.scoring_calibration import (
    ECONOMIC,
    SCORING_CALIBRATION_VERSION,
)
from schemas.assessment_state import Provenance, RangeEstimate

# All thresholds come from the calibration registry (spec 27) — no magic
# numbers in the scoring layer.
W_PAYBACK = ECONOMIC["weight_payback"].value
W_BENEFIT_COST = ECONOMIC["weight_benefit_cost"].value
PAYBACK_FULL_SCORE_AT = ECONOMIC["payback_full_score_months"].value
PAYBACK_ZERO_SCORE_AT = ECONOMIC["payback_zero_score_months"].value
BC_SATURATES_AT = ECONOMIC["benefit_cost_saturation"].value


def payback_component(payback_months: Optional[float]) -> float:
    if payback_months is None:
        return 0.0
    if payback_months <= PAYBACK_FULL_SCORE_AT:
        return 100.0
    if payback_months >= PAYBACK_ZERO_SCORE_AT:
        return 0.0
    span = PAYBACK_ZERO_SCORE_AT - PAYBACK_FULL_SCORE_AT
    return clamp(100.0 * (PAYBACK_ZERO_SCORE_AT - payback_months) / span)


def benefit_cost_component(savings: float, first_year_cost: float) -> float:
    if first_year_cost <= 0:
        return 0.0
    ratio = savings / first_year_cost
    return clamp(100.0 * ratio / BC_SATURATES_AT)


def _score_at(payback: Optional[float], savings: float, cost: float) -> float:
    return (W_PAYBACK * payback_component(payback) +
            W_BENEFIT_COST * benefit_cost_component(savings, cost))


def economic_score(fy: FirstYearEconomics) -> Score:
    payback_mid = midpoint(fy.payback_months) if fy.payback_months else None
    savings_mid = midpoint(fy.annual_cost_savings)
    cost_mid = midpoint(fy.first_year_ai_cost)

    pb = payback_component(payback_mid)
    bc = benefit_cost_component(savings_mid, cost_mid)
    value = W_PAYBACK * pb + W_BENEFIT_COST * bc

    # Bounds from input bounds: best case = fastest payback + best ratio.
    best = _score_at(fy.payback_months.min if fy.payback_months else None,
                     fy.annual_cost_savings.max, fy.first_year_ai_cost.min)
    worst = _score_at(fy.payback_months.max if fy.payback_months else None,
                      fy.annual_cost_savings.min, fy.first_year_ai_cost.max)

    payback_basis = (f"{fy.payback_months.min:.1f}-{fy.payback_months.max:.1f} months"
                     if fy.payback_months else "no positive payback")

    # S3: classify plausibility BEFORE presenting the score. The score is not
    # capped — it is flagged, so a high value cannot read as strong evidence.
    sanity = sanity_mod.assess(fy)
    flags = [f"{f.code}: {f.statement}" for f in sanity.flags]
    note = "grounded in the labor baseline; benchmark figures do not feed this score"
    if not sanity.presentable_as_strong:
        note += (f". SANITY {sanity.level.value.upper()}: this score must not be "
                 f"presented as high-confidence evidence of economic "
                 f"attractiveness — see flags.")
    if sanity.outcome.value == "range_crossing":
        note += " Economic outcome is RANGE CROSSING."

    return Score(
        key="economic", label="Economic Score", value=round(value, 1),
        bounds=RangeEstimate(min=round(min(worst, best), 1), max=round(max(worst, best), 1),
                             confidence=fy.annual_cost_savings.confidence,
                             provenance=Provenance.DERIVED,
                             source="economic score recomputed at input bounds"),
        band=band_for(value), flags=flags,
        bounds_type=BoundsType.NUMERIC_INPUT_ENVELOPE,
        inputs_varied=["payback_months", "annual_cost_savings", "first_year_ai_cost"],
        inputs_held_fixed=[],
        calibration_version=SCORING_CALIBRATION_VERSION,
        sub_scores=[
            SubScore(key="payback", label="Payback period", value=round(pb, 1),
                     weight=W_PAYBACK, basis=payback_basis,
                     note=f"<= {PAYBACK_FULL_SCORE_AT:.0f} months scores 100; "
                          f">= {PAYBACK_ZERO_SCORE_AT:.0f} scores 0"),
            SubScore(key="benefit_cost", label="Benefit-to-cost ratio", value=round(bc, 1),
                     weight=W_BENEFIT_COST,
                     basis=f"{savings_mid:,.0f} annual savings / {cost_mid:,.0f} first-year cost",
                     note=f"saturates at a ratio of {BC_SATURATES_AT}"),
        ],
        note=note,
    )
