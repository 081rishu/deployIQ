"""Sensitivity interface — spec 8.10, E7.

Bounds come from each input's OWN range, not an arbitrary +/-30%. Sweeping a
sourced figure by 30% when its real spread is 2% manufactures uncertainty;
sweeping a wild assumption by 30% understates it. Every variable therefore
declares baseline / min / max / provenance / source, and the sweep recalculates
the economic model at exactly those bounds.

Where an input genuinely has no defensible range, an explicit assumption range
is declared and labelled — never invented silently.

This module recalculates. It does NOT rank variables, produce drivers, compute
a score, or recommend anything: that boundary is what stops the engine
becoming a recommender.
"""

from __future__ import annotations

from typing import Callable, Optional

from pydantic import BaseModel, Field

from calc import calibration
from calc.ai_state import LaborRealization
from calc.engine import EconomicInputError, EconomicResult, Overrides, run
from calc.models import midpoint
from schemas.assessment_state import point, AssessmentState, Provenance, RangeEstimate
from solution.schema import SolutionEstimate


class SensitivityVariable(BaseModel):
    key: str                      # matching an Overrides field
    label: str
    baseline: float
    min: float
    max: float
    unit: str = "multiplier"
    provenance: Provenance = Provenance.ASSUMED
    source: str = ""

    @property
    def has_range(self) -> bool:
        return self.max > self.min


class VariableImpact(BaseModel):
    variable: str
    label: str
    provenance: Provenance
    source: str
    baseline_metric: float
    low_metric: float
    high_metric: float
    swing: float
    direction: str
    bounds: str = ""
    failed: Optional[str] = None


class SensitivityReport(BaseModel):
    metric: str
    baseline: float
    impacts: list[VariableImpact] = Field(default_factory=list)
    skipped: list[str] = Field(default_factory=list)
    note: str = (
        "Bounds are each input's own range, not a uniform perturbation. "
        "Recalculation only — ranking these by importance belongs to the "
        "Decision Driver module, not the Economic Engine."
    )


def annual_savings(r: EconomicResult) -> float:
    return midpoint(r.first_year.annual_cost_savings)


def first_year_net_benefit(r: EconomicResult) -> float:
    return midpoint(r.first_year.first_year_net_benefit)


def _ratio_bounds(rng: RangeEstimate) -> Optional[tuple[float, float, float]]:
    """Turn an absolute range into multipliers around its own midpoint."""
    mid = midpoint(rng)
    if mid <= 0:
        return None
    return 1.0, rng.min / mid, rng.max / mid


def build_variables(
    state: AssessmentState, solution: SolutionEstimate,
) -> list[SensitivityVariable]:
    """Declare each sweepable variable with its REAL bounds (E7)."""
    out: list[SensitivityVariable] = []

    auto = solution.overall_automation
    b = _ratio_bounds(auto)
    if b:
        out.append(SensitivityVariable(
            key="automation_scale", label="Automation rate",
            baseline=b[0], min=b[1], max=b[2],
            unit=f"{auto.min:.0f}-{auto.max:.0f}% (estimator range)",
            provenance=auto.provenance, source=auto.source or "estimator estimate"))

    hours = solution.engineering_hours
    b = _ratio_bounds(hours)
    if b:
        out.append(SensitivityVariable(
            key="implementation_scale", label="Implementation effort",
            baseline=b[0], min=b[1], max=b[2],
            unit=f"{hours.min:.0f}-{hours.max:.0f} hrs (effort band)",
            provenance=hours.provenance, source=hours.source or "effort band"))

    # Review fraction: the calibrated range for the architecture's dominant
    # HITL mode, carrying its own rationale rather than a made-up spread.
    modes = [t.hitl.value for t in solution.task_automation]
    dominant = max(set(modes), key=modes.count) if modes else "human_review"
    cal = calibration.review_fraction_for(dominant)
    if cal.max > cal.min:
        out.append(SensitivityVariable(
            key="review_fraction", label="Human review rate",
            baseline=cal.mid, min=cal.min, max=cal.max,
            unit="fraction of full handling time",
            provenance=Provenance.ASSUMED,
            source=f"calibration [{cal.calibration_id}]: {cal.rationale}"))

    # Labor rate: the pack's own loading-multiplier spread when the rate is
    # benchmark-derived; skipped entirely when the user gave a firm figure.
    if not point(state.fully_loaded_annual_cost):
        from lib.benchmarks import figure as bfig
        loading = bfig(state.sector, "fully_loaded_multiplier")
        if loading is not None:
            lo, hi = loading.bounds
            mid = (lo + hi) / 2.0
            out.append(SensitivityVariable(
                key="labor_rate_scale", label="Labor rate",
                baseline=1.0, min=lo / mid, max=hi / mid,
                unit=f"employer load {lo}-{hi}x",
                provenance=Provenance.ASSUMED, source=loading.citation()))
    return out


def sweep(
    state: AssessmentState,
    solution: SolutionEstimate,
    labor_realization: LaborRealization,
    metric: Callable[[EconomicResult], float] = first_year_net_benefit,
    metric_name: str = "first_year_net_benefit",
    variables: Optional[list[SensitivityVariable]] = None,
) -> SensitivityReport:
    baseline_result = run(state, solution, labor_realization)
    baseline = metric(baseline_result)
    report = SensitivityReport(metric=metric_name, baseline=baseline)

    for var in variables or build_variables(state, solution):
        if not var.has_range:
            report.skipped.append(
                f"{var.label}: no defensible range — not swept rather than "
                f"assigned an invented one")
            continue
        try:
            lo = metric(run(state, solution, labor_realization,
                            Overrides(**{var.key: var.min})))
            hi = metric(run(state, solution, labor_realization,
                            Overrides(**{var.key: var.max})))
        except (EconomicInputError, ValueError, ZeroDivisionError) as exc:
            report.impacts.append(VariableImpact(
                variable=var.key, label=var.label, provenance=var.provenance,
                source=var.source, baseline_metric=baseline, low_metric=baseline,
                high_metric=baseline, swing=0.0, direction="not computable",
                failed=str(exc)))
            continue
        report.impacts.append(VariableImpact(
            variable=var.key, label=var.label, provenance=var.provenance,
            source=var.source, baseline_metric=baseline, low_metric=lo,
            high_metric=hi, swing=abs(hi - lo), bounds=var.unit,
            direction=("increases" if hi > lo else
                       "decreases" if hi < lo else "no effect")))
    return report
