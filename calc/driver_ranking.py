"""Decision Drivers and the uncertainty callout — spec 9.5.  [FROZEN 2026-08-19]

FROZEN: the scoring layer is complete. Do not add scoring features. No score
may produce a recommendation, and no LLM may choose drivers.

This is the product's primary output, and it is a CALCULATION, not a judgement:

    elasticity = (% change in outcome) / (% change in input)

Each candidate variable is swept through its own bounds, every affected score
is recomputed deterministically, and the elasticity is measured. Dividing by
the input change is what makes variables with different units and different
sweep widths comparable — a raw swing comparison would simply rank whichever
variable happened to be swept furthest.

    uncertainty index = relative range width x elasticity

A wide range on a variable nothing depends on is not the biggest uncertainty,
and neither is a precisely known variable that dominates the outcome. The
callout needs both.

The LLM phrases these. It does not choose which facts appear, and every
statement below is generated in code from calculated values.

BOUNDARY: this module ranks facts. It does not emit a verdict, a category, or
a recommendation, and must never learn to.
"""

from __future__ import annotations

from enum import Enum
from typing import Callable, Optional

from pydantic import BaseModel, Field

from calc import composite as composite_mod
from calc import economic_score as econ_mod
from calc import feasibility_score as feas_mod
from calc import risk_score as risk_mod
from calc import assessment_confidence
from calc import calibration
from calc import feasibility_score as feas_mod
from calc.ai_state import LaborRealization
from calc.engine import EconomicResult, Overrides, run
from calc import scoring_calibration as SCORING_CAL
from calc import uncertainty as unc
from calc.models import Score, midpoint
from schemas.assessment_state import (
    point,
    AssessmentState,
    DataReadiness,
    EffortBand,
    RangeEstimate,
)
from solution.schema import SolutionEstimate

# Elasticity below this is treated as "does not move the outcome".
NEGLIGIBLE_ELASTICITY = 0.01


class DriverType(str, Enum):
    """S10: what KIND of statement a driver is.

    A finding about our data coverage must not read as a finding about the
    business.
    """
    BUSINESS_FACT = "business_fact"
    MODEL_ESTIMATE = "model_estimate"
    DATA_COVERAGE = "data_coverage"
    UNCERTAINTY = "uncertainty"


def _quantities(bundle: "ScoreBundle") -> dict[str, Optional[float]]:
    """The UNBOUNDED economic quantities drivers are ranked against (S1).

    Scores are bounded and saturating: near a ceiling their derivative
    collapses, so an influential variable registers as irrelevant. Measured
    directly, labor-rate elasticity moved 22x between a saturated and an
    unsaturated score for the same underlying business. These quantities do
    not saturate.
    """
    fy = bundle.result.first_year
    return {
        "annual_benefit": midpoint(fy.annual_cost_savings),
        "first_year_net_benefit": midpoint(fy.first_year_net_benefit),
        "payback": (midpoint(fy.payback_months) if fy.payback_months else None),
    }


class ScoreBundle(BaseModel):
    economic: Score
    feasibility: Score
    risk: Score
    composite: Score
    result: EconomicResult
    # Spec 9.7: confidence is a separate axis from score magnitude.
    confidence: Optional[dict] = None

    def value(self, key: str) -> Optional[float]:
        return getattr(self, key).value


def compute_scores(
    state: AssessmentState, solution: SolutionEstimate,
    realization: LaborRealization, overrides: Optional[Overrides] = None,
) -> ScoreBundle:
    """Run the economic engine and all four scores over one set of inputs."""
    result = run(state, solution, realization, overrides)
    economic = econ_mod.economic_score(result.first_year)
    feasibility = feas_mod.feasibility_score(solution, state.data_readiness)
    achievable = _achievable_accuracy(solution)
    required = _required_accuracy(state)
    risk = risk_mod.risk_score(state, solution, required_accuracy=required,
                               achievable_accuracy=achievable)
    assumed = sum(1 for line in result.ai_operating.known_lines
                  if line.amount is not None
                  and line.amount.provenance.value == "assumed")
    evidence_backed = len(result.inference_pricing_ids)
    conf = assessment_confidence.assess(state, solution,
                                        evidence_backed=evidence_backed,
                                        assumed_inputs=assumed)
    return ScoreBundle(economic=economic, feasibility=feasibility, risk=risk,
                       composite=composite_mod.composite_score(economic, feasibility, risk),
                       result=result, confidence=conf.model_dump(mode="json"))


def _achievable_accuracy(solution: SolutionEstimate) -> Optional[RangeEstimate]:
    for pm in solution.performance:
        if pm.metric in risk_mod._ACCURACY_METRICS:
            e = pm.estimate
            return RangeEstimate(min=e.min / 100.0, max=e.max / 100.0,
                                 confidence=e.confidence, provenance=e.provenance,
                                 source=pm.metric)
    return None


def _required_accuracy(state: AssessmentState) -> Optional[RangeEstimate]:
    from calc.engine import _as_range
    r = _as_range(point(state.required_accuracy), "required accuracy")
    if r is not None and r.max > 1.0:
        return RangeEstimate(min=r.min / 100.0, max=r.max / 100.0,
                             confidence=r.confidence, provenance=r.provenance,
                             source=r.source)
    return r


# --------------------------------------------------------------------------
# Candidate variables
# --------------------------------------------------------------------------

Mutator = Callable[[AssessmentState, SolutionEstimate, Overrides],
                   tuple[AssessmentState, SolutionEstimate, Overrides]]


class DriverVariable(BaseModel):
    key: str
    label: str
    # Input values at the low and high ends, in the variable's own units. Used
    # as the denominator of the sensitivity, so they must be real values.
    low_input: float
    high_input: float
    baseline_input: float
    uncertainty: Optional[unc.Uncertainty] = None
    driver_type: DriverType = DriverType.MODEL_ESTIMATE
    confidence: str = "medium"
    provenance: str = "estimated"
    evidence_ids: list[str] = Field(default_factory=list)
    evidence_note: str = ""
    statement: str = ""             # factual rendering, generated in code
    mutate_low: Optional[object] = None
    mutate_high: Optional[object] = None

    model_config = {"arbitrary_types_allowed": True}


def _scaled(key: str, factor: float) -> Mutator:
    def f(state, solution, ov):
        return state, solution, ov.model_copy(update={key: factor})
    return f


def _set_review(fraction: float) -> Mutator:
    def f(state, solution, ov):
        return state, solution, ov.model_copy(update={"review_fraction": fraction})
    return f


def _set_readiness(level: DataReadiness) -> Mutator:
    def f(state, solution, ov):
        return state.model_copy(update={"data_readiness": level}), solution, ov
    return f


def _set_integration(band: EffortBand) -> Mutator:
    def f(state, solution, ov):
        return state, solution.model_copy(update={"integration_complexity": band}), ov
    return f


def candidate_variables(
    state: AssessmentState, solution: SolutionEstimate, result: EconomicResult,
) -> list[DriverVariable]:
    """Build the sweepable set, each with its OWN bounds and typed uncertainty.

    S2: a categorical input carries a category + confidence, never a synthetic
    numeric width. No variable receives an invented +/-15% or +/-30%.
    """
    out: list[DriverVariable] = []

    auto = solution.overall_automation
    base_auto = midpoint(auto)
    if base_auto > 0:
        ev = [t.benchmark_anchor for t in solution.task_automation if t.benchmark_anchor]
        out.append(DriverVariable(
            key="automation_rate", label="Automation rate",
            low_input=auto.min, high_input=auto.max, baseline_input=base_auto,
            uncertainty=unc.from_range("automation_rate", auto),
            driver_type=DriverType.MODEL_ESTIMATE, confidence=auto.confidence,
            provenance=auto.provenance.value,
            evidence_ids=[e for e in ev if e][:2],
            evidence_note=("benchmark context attached to the task estimates"
                           if ev else ""),
            statement=f"Expected automation is estimated at "
                      f"{auto.min:.0f}-{auto.max:.0f}%.",
            mutate_low=_scaled("automation_scale", auto.min / base_auto),
            mutate_high=_scaled("automation_scale", auto.max / base_auto),
        ))

    hours = solution.engineering_hours
    base_hours = midpoint(hours)
    if base_hours > 0:
        out.append(DriverVariable(
            key="implementation_effort", label="Implementation effort",
            low_input=hours.min, high_input=hours.max, baseline_input=base_hours,
            uncertainty=unc.from_range("implementation_effort", hours),
            driver_type=DriverType.MODEL_ESTIMATE, confidence=hours.confidence,
            provenance=hours.provenance.value,
            statement=(f"Implementation effort is the "
                       f"{solution.engineering_effort.value} band, "
                       f"{hours.min:.0f}-{hours.max:.0f} hours."),
            mutate_low=_scaled("implementation_scale", hours.min / base_hours),
            mutate_high=_scaled("implementation_scale", hours.max / base_hours),
        ))

    # Human review: a real calibrated assumption range, carrying that label.
    review_lines = [t for t in result.tasks if t.human_review_cost is not None]
    if review_lines:
        cal = calibration.REVIEW_FRACTION_BY_HITL["human_review"]
        out.append(DriverVariable(
            key="review_fraction", label="Human review rate",
            low_input=cal.min, high_input=cal.max, baseline_input=cal.mid,
            uncertainty=unc.from_range("review_fraction", cal.as_range()),
            driver_type=DriverType.MODEL_ESTIMATE, confidence="low",
            provenance="assumed",
            statement=(f"Human review remains necessary on {len(review_lines)} of "
                       f"{len(result.tasks)} tasks, costed at "
                       f"{cal.min:.0%}-{cal.max:.0%} of full handling time."),
            mutate_low=_set_review(cal.min), mutate_high=_set_review(cal.max),
        ))

    # S10: a DATA-COVERAGE fact, worded so it cannot read as a business finding.
    absent = result.current_annual_cost.absent_lines
    if absent:
        labels = ", ".join(l.label.lower() for l in absent)
        out.append(DriverVariable(
            key="cost_coverage", label="Current-cost coverage",
            low_input=1.0, high_input=1.0, baseline_input=1.0,
            uncertainty=None, driver_type=DriverType.DATA_COVERAGE,
            confidence="low", provenance="assumed",
            statement=(f"Labor represents the entire measured current cost "
                       f"because only labor cost was supplied — {labels} were "
                       f"not provided, so the baseline is a floor."),
        ))

    # Categorical inputs: category + confidence, NO synthetic width (S2-B).
    if state.data_readiness is not None:
        levels = list(DataReadiness)
        idx = levels.index(state.data_readiness)
        lo_level = levels[max(0, idx - 1)]
        hi_level = levels[min(len(levels) - 1, idx + 1)]
        base_v = feas_mod.READINESS_SCORES[state.data_readiness]
        out.append(DriverVariable(
            key="data_readiness", label="Data readiness",
            low_input=feas_mod.READINESS_SCORES[lo_level],
            high_input=feas_mod.READINESS_SCORES[hi_level],
            baseline_input=base_v or 1.0,
            uncertainty=unc.categorical("data_readiness",
                                        state.data_readiness.value, "medium"),
            driver_type=DriverType.BUSINESS_FACT, confidence="medium",
            provenance="user_provided",
            statement=f"Data readiness is reported as {state.data_readiness.value}.",
            mutate_low=_set_readiness(lo_level), mutate_high=_set_readiness(hi_level),
        ))

    if solution.integration_complexity is not None:
        bands = list(EffortBand)
        idx = bands.index(solution.integration_complexity)
        lo_band = bands[min(len(bands) - 1, idx + 1)]
        hi_band = bands[max(0, idx - 1)]
        base_v = feas_mod.INTEGRATION_SCORES[solution.integration_complexity]
        out.append(DriverVariable(
            key="integration_complexity", label="Integration complexity",
            low_input=feas_mod.INTEGRATION_SCORES[lo_band],
            high_input=feas_mod.INTEGRATION_SCORES[hi_band],
            baseline_input=base_v or 1.0,
            uncertainty=unc.categorical("integration_complexity",
                                        solution.integration_complexity.value,
                                        "medium"),
            driver_type=DriverType.BUSINESS_FACT, confidence="medium",
            provenance="derived",
            statement=(f"Integration complexity is assessed as "
                       f"{solution.integration_complexity.value}."),
            mutate_low=_set_integration(lo_band),
            mutate_high=_set_integration(hi_band),
        ))
    return out


class DriverImpact(BaseModel):
    key: str
    label: str
    statement: str
    driver_type: DriverType = DriverType.MODEL_ESTIMATE
    # S1: impact against UNBOUNDED economic quantities.
    impact: float = 0.0
    per_quantity: dict[str, float] = Field(default_factory=dict)
    payback_status: str = "ok"
    dominant_quantity: str = ""
    confidence: str = "medium"
    provenance: str = "estimated"
    # S2: typed uncertainty. `relative_width` is None for categorical inputs.
    uncertainty_type: str = "none"
    relative_width: Optional[float] = None
    uncertainty_index: Optional[float] = None
    # S11: evidence context, which informs confidence but never rank.
    evidence_ids: list[str] = Field(default_factory=list)
    evidence_note: str = ""


class DecisionDrivers(BaseModel):
    drivers: list[DriverImpact] = Field(default_factory=list)
    uncertainty_callout: Optional[DriverImpact] = None
    uncertainty_statement: str = ""
    scores: ScoreBundle
    method: str = (
        "Drivers ranked by sensitivity of the UNDERLYING ECONOMIC QUANTITIES "
        "(annual benefit, first-year net benefit, payback) to each input — not "
        "by score elasticity, because bounded scores saturate and would erase "
        "important drivers near a ceiling. Payback is handled threshold-aware "
        "and contributes nothing when undefined. The uncertainty callout "
        "combines relative range width with decision impact, and only "
        "numeric-range inputs receive a numeric width. No verdict, category or "
        "recommendation is produced (spec 9.8)."
    )


def _elasticity(base_out: float, out_lo: float, out_hi: float,
                base_in: float, in_lo: float, in_hi: float) -> float:
    if base_out == 0 or base_in == 0:
        return 0.0
    d_out = (out_hi - out_lo) / abs(base_out)
    d_in = (in_hi - in_lo) / abs(base_in)
    if d_in == 0:
        return 0.0
    return abs(d_out / d_in)


def _payback_sensitivity(base, lo, hi) -> tuple[Optional[float], str]:
    """Payback needs threshold-aware handling, not blind elasticity (spec 3).

    It can approach zero, become undefined, or cross from positive to negative
    benefit. Where any bound is undefined, no elasticity is manufactured.
    """
    values = [base, lo, hi]
    if any(v is None for v in values):
        return None, "non_positive_or_indeterminate"
    if base == 0:
        return None, "non_positive_or_indeterminate"
    return abs((hi - lo) / base), "ok"


def _combined_impact(per_quantity: dict[str, float]) -> float:
    """Weighted blend across the quantities that are actually available.

    A missing quantity is EXCLUDED from the blend, never treated as zero —
    zero would silently say "this variable does not matter" when the truth is
    "we could not measure it here".
    """
    weights = {k: SCORING_CAL.DRIVER_IMPACT_WEIGHTS[k].value
               for k in SCORING_CAL.DRIVER_IMPACT_WEIGHTS}
    usable = {k: v for k, v in per_quantity.items() if v is not None}
    if not usable:
        return 0.0
    total_w = sum(weights.get(k, 0.0) for k in usable) or 1.0
    return sum(v * weights.get(k, 0.0) for k, v in usable.items()) / total_w


def rank_drivers(
    state: AssessmentState, solution: SolutionEstimate,
    realization: LaborRealization, top_n: int = 5,
) -> DecisionDrivers:
    base = compute_scores(state, solution, realization)
    base_q = _quantities(base)
    variables = candidate_variables(state, solution, base.result)

    impacts: list[DriverImpact] = []
    for var in variables:
        # A data-coverage fact has no sweep; it is reported, not ranked by
        # sensitivity, and is never given a fabricated impact number.
        if var.mutate_low is None or var.mutate_high is None:
            impacts.append(DriverImpact(
                key=var.key, label=var.label, statement=var.statement,
                driver_type=var.driver_type, impact=0.0,
                confidence=var.confidence, provenance=var.provenance,
                uncertainty_type=(var.uncertainty.uncertainty_type.value
                                  if var.uncertainty else "none"),
                evidence_ids=var.evidence_ids, evidence_note=var.evidence_note))
            continue
        try:
            s_lo, sol_lo, ov_lo = var.mutate_low(state, solution, Overrides())
            s_hi, sol_hi, ov_hi = var.mutate_high(state, solution, Overrides())
            lo = _quantities(compute_scores(s_lo, sol_lo, realization, ov_lo))
            hi = _quantities(compute_scores(s_hi, sol_hi, realization, ov_hi))
        except (ValueError, ZeroDivisionError):
            continue

        per_q: dict[str, float] = {}
        payback_status = "ok"
        for q in ("annual_benefit", "first_year_net_benefit"):
            b, l, h = base_q[q], lo[q], hi[q]
            if b is None or l is None or h is None:
                continue
            per_q[q] = round(_elasticity(b, l, h, var.baseline_input,
                                         var.low_input, var.high_input), 4)
        pb, payback_status = _payback_sensitivity(
            base_q["payback"], lo["payback"], hi["payback"])
        if pb is not None and var.high_input != var.low_input:
            d_in = abs((var.high_input - var.low_input) / var.baseline_input)
            per_q["payback"] = round(pb / d_in, 4) if d_in else 0.0

        if not per_q:
            continue
        impact = _combined_impact(per_q)
        dominant = max(per_q, key=lambda k: per_q[k])
        u = var.uncertainty
        width = u.relative_width if u else None
        idx = (round(width * impact, 4) if width is not None else None)

        impacts.append(DriverImpact(
            key=var.key, label=var.label, statement=var.statement,
            driver_type=var.driver_type, impact=round(impact, 4),
            per_quantity=per_q, payback_status=payback_status,
            dominant_quantity=dominant, confidence=var.confidence,
            provenance=var.provenance,
            uncertainty_type=(u.uncertainty_type.value if u else "none"),
            relative_width=(round(width, 4) if width is not None else None),
            uncertainty_index=idx, evidence_ids=var.evidence_ids,
            evidence_note=var.evidence_note))

    ranked = sorted([i for i in impacts if i.driver_type != DriverType.DATA_COVERAGE],
                    key=lambda i: i.impact, reverse=True)
    coverage = [i for i in impacts if i.driver_type == DriverType.DATA_COVERAGE]
    drivers = ranked[:top_n] + coverage[:1]

    # S2/S9: the callout needs uncertainty AND impact. Only numeric-range
    # inputs carry a numeric index; a categorical factor is reported through a
    # separate, honestly-worded route.
    numeric = [i for i in impacts if i.uncertainty_index is not None
               and i.uncertainty_index > 0]
    callout = max(numeric, key=lambda i: i.uncertainty_index, default=None)
    statement = ""
    if callout is not None:
        statement = (
            f"Most decision-sensitive uncertainty: {callout.label.lower()}. Its "
            f"estimated range spans {callout.relative_width:.0%} of its own "
            f"value, and that range materially changes "
            f"{callout.dominant_quantity.replace('_', ' ')}. Narrowing it would "
            f"change the assessment more than narrowing anything else.")
    else:
        categorical_unresolved = [i for i in impacts
                                  if i.uncertainty_type == "categorical"]
        if categorical_unresolved:
            c = max(categorical_unresolved, key=lambda i: i.impact)
            statement = (
                f"Most decision-sensitive unresolved factor: {c.label.lower()}. "
                f"It is a categorical input, so it has no numeric range — moving "
                f"it to an adjacent level changes "
                f"{c.dominant_quantity.replace('_', ' ') or 'the assessment'} "
                f"materially.")

    return DecisionDrivers(drivers=drivers, uncertainty_callout=callout,
                           uncertainty_statement=statement, scores=base)
