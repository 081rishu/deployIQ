"""Economic Engine orchestrator — spec 8.  [FROZEN 2026-08-19]

FROZEN: this layer is complete. Do not add economic features. Changes are for
mathematical or accounting defects only, evidenced by a failing test.

Explicitly NOT in this layer, and never to be added: LLM calls, score
calculation, Decision Driver ranking, architecture selection, recommendation
logic, or multi-year financial modelling.


    AssessmentState + SolutionEstimate + labor realization policy
        -> EconomicResult

No LLM anywhere in this layer. Everything the LLM contributed (automation
ranges, effort band) arrives pre-tagged as an estimate and is treated as an
assumption with provenance, exactly as spec 8 requires.

The engine calculates consequences. It does not judge them.
"""

from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, Field

from calc import (
    ai_state,
    calibration,
    benchmark_check,
    current_state,
    implementation,
    labor,
    lifecycle,
    quality as quality_mod,
)
from calc import inference as inference_mod
from calc import reliability as reliability_mod
from calc.ai_state import LaborRealization, TaskEconomics
from calc.models import CostBreakdown, add, div, midpoint, money, scale
from schemas.assessment_state import (
    point,
    AssessmentState,
    Provenance,
    RangeEstimate,
    Sector,
)
from solution.schema import HitlMode, SolutionEstimate

# Performance metrics that represent "share of output that is acceptable".
_QUALITY_METRICS = ("extraction_accuracy", "answer_accuracy", "resolution_rate")


class EconomicInputError(ValueError):
    """The engine cannot compute a defensible result from these inputs.

    Raised rather than returning a manufactured baseline. `reasons` is what the
    caller should surface as needs_more_information.
    """

    def __init__(self, reasons: list[str]):
        self.reasons = reasons
        super().__init__("; ".join(reasons))


class Overrides(BaseModel):
    """Multiplicative levers, used by the sensitivity interface (spec 8.10).
    All default to no-ops so a baseline run is unperturbed."""
    automation_scale: float = 1.0
    labor_rate_scale: float = 1.0
    handling_time_scale: float = 1.0
    volume_scale: float = 1.0
    implementation_scale: float = 1.0
    review_fraction: Optional[float] = None


class EconomicResult(BaseModel):
    sector: Sector
    labor_realization: LaborRealization
    realization_statement: str

    labor_consistency: labor.LaborConsistency
    baseline_basis: str
    # D3: shared with the Solution Estimator (finesse spec 2).
    time_reconciliation: Optional[dict] = None
    current_annual_cost: CostBreakdown
    current_annual_total: RangeEstimate

    tasks: list[TaskEconomics] = Field(default_factory=list)
    ai_operating: CostBreakdown
    ai_operating_total: RangeEstimate
    freed_capacity_value: Optional[RangeEstimate] = None

    implementation: CostBreakdown
    implementation_total: RangeEstimate

    first_year: lifecycle.FirstYearEconomics
    unit_economics: lifecycle.UnitEconomics
    benchmark: benchmark_check.BenchmarkCrossCheck
    quality_comparison: Optional[dict] = None
    reliability: Optional[dict] = None
    labor_rate_geography: Optional[str] = None
    inference_pricing_ids: list[str] = Field(default_factory=list)
    inference_lineage: list[str] = Field(default_factory=list)
    # Section 2: which provenance kinds fed each derived line. Interval
    # arithmetic collapses inputs into a single DERIVED value, so the mix is
    # recorded separately — `estimated` and `assumed` are both uncertain but
    # they are not the same kind of evidence and must stay distinguishable.
    provenance_lineage: dict[str, list[str]] = Field(default_factory=dict)

    warnings: list[str] = Field(default_factory=list)
    absent_components: list[str] = Field(default_factory=list)


def _as_range(value, label: str) -> Optional[RangeEstimate]:
    """Coerce a state field to a range.

    Tolerates the dict-in-a-float-field drift on `required_accuracy` (the
    interviewer writes {'min':..,'max':..} into a field declared Optional[float]
    and set_value bypasses pydantic validation). Recorded as C11 in
    docs/estimator_todo.md; handled defensively here so the engine reports the
    problem instead of raising TypeError mid-calculation.
    """
    if value is None:
        return None
    if isinstance(value, dict) and "min" in value and "max" in value:
        return RangeEstimate(min=float(value["min"]), max=float(value["max"]),
                             confidence="medium", provenance=Provenance.USER_PROVIDED,
                             source=f"user-provided {label} (range)")
    if isinstance(value, RangeEstimate):
        return value
    if isinstance(value, (int, float)):
        return money(float(value), provenance=Provenance.USER_PROVIDED,
                     confidence="high", source=f"user-provided {label}")
    return None


def _expected_ai_accuracy(solution: SolutionEstimate) -> Optional[RangeEstimate]:
    for pm in solution.performance:
        if pm.metric in _QUALITY_METRICS:
            e = pm.estimate
            return RangeEstimate(min=e.min / 100.0, max=e.max / 100.0,
                                 confidence=e.confidence, provenance=e.provenance,
                                 source=f"{pm.metric}: {e.source}")
    return None


def _fraction_time(state: AssessmentState) -> Optional[RangeEstimate]:
    if point(state.fraction_time_on_process) is not None:
        return money(float(point(state.fraction_time_on_process)),
                     provenance=Provenance.USER_PROVIDED, confidence="high",
                     source="user-provided fraction of time on process")
    from lib.benchmarks import figure as bfig
    fig = bfig(state.sector, "fraction_time_on_process")
    return fig.as_range() if fig else None


def run(
    state: AssessmentState,
    solution: SolutionEstimate,
    labor_realization: LaborRealization,
    overrides: Optional[Overrides] = None,
) -> EconomicResult:
    """Run the economic model.

    `labor_realization` is required, with no default: spec 8.4 forbids silently
    deciding whether freed capacity becomes money.
    """
    ov = overrides or Overrides()
    warnings: list[str] = []

    # --- 8.1 labor -------------------------------------------------------
    rate = labor.resolve_labor_rate(state)
    if rate.status == labor.LaborRateStatus.UNRESOLVED:
        # E8: no silent US fallback.
        raise EconomicInputError([f"labor rate unresolved — {rate.statement}"])
    hourly = scale(rate.hourly, ov.labor_rate_scale) if ov.labor_rate_scale != 1.0 else rate.hourly
    annual_loaded = (scale(rate.annual_loaded, ov.labor_rate_scale)
                     if ov.labor_rate_scale != 1.0 else rate.annual_loaded)
    if rate.basis == "benchmark_derived":
        warnings.append(
            "labor rate is derived from the sector benchmark pack, not from the "
            "organisation's own payroll — the single largest assumption in the "
            "baseline")

    volume = labor.annual_volume(state)
    if volume is not None and ov.volume_scale != 1.0:
        volume = scale(volume, ov.volume_scale)

    # D3 (shared policy): the observed aggregate is the authoritative baseline.
    # Task times from the estimator supply proportions only, so the engine and
    # the estimator cannot disagree about total handling time.
    task_times = [ (t.handling_time_minutes.min + t.handling_time_minutes.max) / 2.0
                   if t.handling_time_minutes else None
                   for t in solution.task_automation ]
    task_names = [t.task for t in solution.task_automation]
    reconciliation = labor.authoritative_handling_time(state, task_times, task_names)
    if reconciliation.blocks_estimate:
        warnings.append(reconciliation.statement)
    for w in reconciliation.warnings:
        warnings.append(w)

    handling = _as_range(reconciliation.authoritative_total_minutes, "handling time")
    if handling is not None and ov.handling_time_scale != 1.0:
        handling = scale(handling, ov.handling_time_scale)

    task_based = (labor.task_based_labor_cost(volume, handling, hourly)
                  if volume is not None and handling is not None else None)

    fraction = _fraction_time(state)
    workforce_based = (
        labor.workforce_labor_cost(float(point(state.current_headcount)), annual_loaded, fraction)
        if point(state.current_headcount) and fraction is not None else None)

    # E5: classify, then take the PRIMARY on a stated rule. The engine never
    # picks whichever formulation happens to be larger.
    consistency = labor.check_consistency(task_based, workforce_based)
    if consistency.needs_more_information:
        raise EconomicInputError([consistency.verdict])
    if consistency.status == labor.BaselineStatus.DIVERGENT:
        warnings.append(consistency.verdict)

    baseline = consistency.primary
    basis = consistency.primary_basis

    # --- 8.2 current annual cost -----------------------------------------
    current = current_state.current_annual_cost(baseline, state, volume, hourly)
    current_total = current.total()

    # --- 8.5 implementation ----------------------------------------------
    # Resolve the selected implementation's kind so stage buy/build defaults
    # follow the architecture rather than assuming everything is built.
    selected_kind = None
    try:
        from solution.patterns import pattern as _pat
        _p = _pat(solution.recommended_pattern)
        _i = next(iter(_p.implementations), None)
        selected_kind = _i.kind.value if _i is not None else None
    except (KeyError, StopIteration):
        pass
    impl, maintenance = implementation.implementation_cost(
        state, solution.engineering_effort, selected_kind)
    impl_total = impl.total()
    if ov.implementation_scale != 1.0:
        impl_total = scale(impl_total, ov.implementation_scale)
        if maintenance is not None:
            maintenance = scale(maintenance, ov.implementation_scale)

    # --- 8.3 / 8.4 AI state ----------------------------------------------
    # E9: an unresolved share set must not become a fake equal split.
    if solution.task_automation:
        unresolved = [t for t in solution.task_automation
                      if t.workload_share_provenance == Provenance.ASSUMED]
        if unresolved and len(unresolved) == len(solution.task_automation):
            raise EconomicInputError([
                "task workload shares are an unresolved default, not derived from "
                "handling time. Splitting the baseline by an invented equal share "
                "would create task-level economics that were never measured."])
    share_note = ai_state.share_warning(solution)
    if share_note:
        warnings.append(share_note)

    automation = solution.overall_automation
    if ov.automation_scale != 1.0:
        automation = scale(automation, ov.automation_scale)

    tasks = ai_state.build_tasks(
        solution, baseline, labor_realization,
        review_fraction_override=ov.review_fraction,
        automation_scale=ov.automation_scale)
    if not tasks:
        # No task decomposition: fall back to one blended task so the model is
        # still computable, and say so rather than pretending it was task-level.
        # Defect fix: this fallback passed a raw string where a HitlMode is
        # required, so any estimate without a task decomposition crashed here.
        fallback_hitl = HitlMode.AI_ASSISTED
        if solution.hitl_requirements:
            first = next(iter(solution.hitl_requirements.values()))
            fallback_hitl = first if isinstance(first, HitlMode) else HitlMode(str(first))
        tasks = [ai_state.task_economics(
            "entire process", fallback_hitl, 1.0, baseline, automation,
            labor_realization)]

    # E2: price the architecture that was actually selected.
    provider_ids: list[str] = []
    implementation_id = ""
    try:
        from solution.patterns import pattern as _pattern
        pat = _pattern(solution.recommended_pattern)
        selected_impl = next(iter(pat.implementations), None)
        if selected_impl is not None:
            implementation_id = selected_impl.id
            provider_ids = [pr.id for pr in selected_impl.providers]
    except (KeyError, StopIteration):
        pass

    inference = inference_mod.inference_cost(
        state.sector, solution, volume, implementation_id, provider_ids,
        baseline_currency=rate.currency)
    if inference.currency_mismatch:
        warnings.append(
            f"currency: {inference.currency_mismatch}. Provider pricing was NOT "
            f"converted or added — the AI operating cost excludes inference and is "
            f"therefore a floor.")
    ai_ops = ai_state.ai_annual_operating_cost(
        tasks, state.sector, volume, automation, maintenance,
        inference_line=inference.line)
    ai_ops_total = ai_ops.total()
    freed = ai_state.freed_capacity_total(tasks)

    realization_statement = (
        "Freed capacity is taken as reduced spend: the labor line falls in "
        "proportion to displaced work. This assumes the organisation actually "
        "removes the cost."
        if labor_realization == LaborRealization.COST_ELIMINATED else
        f"Headcount is retained: displaced work becomes capacity, not savings. "
        f"Labor spend is unchanged, and the {midpoint(freed):,.0f} of freed "
        f"labor value is reported as capacity rather than banked as a saving.")

    # --- 8.6 / 8.7 lifecycle ---------------------------------------------
    fy = lifecycle.first_year_economics(current_total, ai_ops_total, impl_total)
    ai_accuracy = _expected_ai_accuracy(solution)
    required = _as_range(point(state.required_accuracy), "required accuracy")
    if required is not None and required.max > 1.0:
        required = scale(required, 1 / 100.0)     # tolerate percent-form input
    if ai_accuracy is None:
        warnings.append("no expected AI accuracy metric — unit economics on valid "
                        "output could not be computed")
    elif required is not None and ai_accuracy.max < required.min:
        warnings.append(
            f"expected AI accuracy ({ai_accuracy.min:.0%}-{ai_accuracy.max:.0%}) is "
            f"below the required bar ({required.min:.0%}-{required.max:.0%}) — the "
            f"economics assume output that this solution may not deliver")

    # E6: current-process quality is ABSENT unless measured — never 100%.
    expected_q = None
    for pm in solution.performance:
        obs = quality_mod.from_estimator_metric(pm.metric, pm.estimate)
        if obs is not None and obs.metric in (
                quality_mod.QualityMetric.EXTRACTION_ACCURACY,
                quality_mod.QualityMetric.ANSWER_ACCURACY,
                quality_mod.QualityMetric.RESOLUTION_RATE):
            expected_q = obs
            break
    # E6: use the collected current-quality metric when the interview captured
    # one. Still never assumes 100% — an uncollected metric stays ABSENT.
    current_q = None
    if state.current_quality_metric is not None and state.current_quality_value is not None:
        current_q = quality_mod.from_collected(
            state.current_quality_metric.value, state.current_quality_value)
    if current_q is None:
        current_q = quality_mod.absent(
            expected_q.metric if expected_q else quality_mod.QualityMetric.EXTRACTION_ACCURACY,
            ("no current-process quality measurement was collected"
             if state.current_quality_metric is None else
             f"'{state.current_quality_metric.value}' has no comparable AI-side "
             f"metric, so no like-for-like comparison is made"))
    quality_comparison = quality_mod.compare(current_q, expected_q)
    if not quality_comparison.comparable:
        warnings.append(quality_comparison.statement)

    # E11: a reliability shortfall becomes an operating line ONLY when its
    # consequence is knowable; otherwise it stays a qualitative risk.
    reliability = reliability_mod.consequence(
        state, required, ai_accuracy, volume, hourly)
    if reliability.line is not None:
        ai_ops.lines.append(reliability.line)
        ai_ops_total = ai_ops.total()
        fy = lifecycle.first_year_economics(current_total, ai_ops_total, impl_total)
    if reliability.statement:
        warnings.append(reliability.statement)

    units = lifecycle.unit_economics(
        current_total, ai_ops_total, fy.first_year_ai_cost, volume, None, ai_accuracy)

    # --- 8.8 benchmark cross-check ---------------------------------------
    per_unit = div(current_total, volume, source="current annual cost / annual volume") \
        if volume is not None else None
    bench = benchmark_check.cross_check(state.sector, per_unit)

    absent = [f"{l.label} ({b.label})" for b in (current, ai_ops, impl)
              for l in b.absent_lines]

    # --- provenance lineage (section 2) ----------------------------------
    def _mix(*values) -> list[str]:
        return sorted({v.provenance.value for v in values if v is not None})

    task_estimates = [t_.estimate for t_ in solution.task_automation]
    review_cal = calibration.REVIEW_FRACTION_BY_HITL["human_review"].as_range()
    maint_cal = calibration.MAINTENANCE_FRACTION.as_range()
    lineage: dict[str, list[str]] = {
        "labor_baseline": _mix(rate.hourly, rate.annual_loaded),
        "automation_rate": _mix(solution.overall_automation),
        "task_automation": _mix(*task_estimates),
        "human_review": sorted(set(_mix(review_cal) + _mix(*task_estimates))),
        "maintenance": _mix(maint_cal, hours if 'hours' in dir() else None),
        "engineering_effort": _mix(solution.engineering_hours),
    }
    if inference.line.amount is not None:
        lineage["inference"] = sorted({"assumed", "sourced"})
    lineage = {k: v for k, v in lineage.items() if v}

    return EconomicResult(
        sector=state.sector, labor_realization=labor_realization,
        realization_statement=realization_statement,
        labor_consistency=consistency, baseline_basis=basis,
        time_reconciliation=reconciliation.model_dump(mode="json"),
        current_annual_cost=current, current_annual_total=current_total,
        tasks=tasks, ai_operating=ai_ops, ai_operating_total=ai_ops_total,
        freed_capacity_value=freed,
        implementation=impl, implementation_total=impl_total,
        first_year=fy, unit_economics=units, benchmark=bench,
        quality_comparison=quality_comparison.model_dump(mode="json"),
        reliability=reliability.model_dump(mode="json"),
        labor_rate_geography=rate.geography,
        inference_pricing_ids=inference.pricing_ids,
        inference_lineage=inference.lineage,
        provenance_lineage=lineage,
        warnings=warnings, absent_components=absent,
    )
