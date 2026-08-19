"""AI Solution & Architecture Estimator — orchestrator.

Boundaries (unchanged, and non-negotiable):
  LLM        decomposes the workflow, estimates per-task automation and
             handling time, explains fit. It never selects a pattern, never
             invents hours or rates, never sets a workload share, and never
             supplies a citation that reaches the output as evidence.
  Registry   constrains the candidate space (patterns.py).
  Reference  provides the sector baseline and the conditions for deviating.
  Code       validates, filters, ranks, scopes, derives and calculates.

Implements the C1-C14 fixes:
  C1  evidence integrity — only registry-backed values may claim `sourced`
  C2  effort band derived from assessed scope, not a registry constant
  C3  effort and rate kept separate; cost derived from both
  C4  automation estimates anchored against benchmark evidence
  C5  workload shares derived from handling time, never LLM-supplied
  C6  workload-weighted interval aggregation, labelled an interval
  C7  reference alignment as a ranking term (implemented in ranking.py)
  C8  capability decomposition validated against the reference
  C9  strict enum capabilities; no substring matching
  C10 interviewer field quality gates confidence and refusal
  C11 canonical RangeEstimate/Provenance only
  C12 confidence on every estimate; operating-cost drivers exposed
  C13 risks mapped to real controls
  C14 integration complexity derived, not asked
"""

from __future__ import annotations

from typing import Optional

from lib.benchmarks import figure as benchmark_figure
from lib.logging_config import get_logger
from schemas.assessment_state import (
    point,
    AssessmentState,
    EffortBand,
    Provenance,
    RangeEstimate,
)
from solution import capabilities as caps_mod
from solution import confidence as confidence_mod
from solution import evidence, risks, scope, workload
from solution.effort_bands import cost_for, hours_for
from solution.patterns import patterns_covering
from solution.performance import metrics_for
from solution.ranking import compliance_verdicts, rank_candidates
from solution.reference_solutions import reference_for
from solution.schema import (
    Capability,
    HitlMode,
    OperatingCostInputs,
    ReferenceComparison,
    SolutionEstimate,
    TaskAutomationEstimate,
)

log = get_logger("solution.estimator")

_REQUIRED_FIELDS = {
    "customer_support": ["process", "monthly_volume", "current_headcount",
                         "avg_time_per_unit_minutes", "required_accuracy"],
    "document_processing": ["process", "monthly_volume",
                            "avg_time_per_unit_minutes", "required_accuracy"],
}

_INFERENCE_KEYS = {
    "document_processing": "invoice_extraction_price_per_page",
    "customer_support": None,
}


def _missing_decision_critical(state: AssessmentState) -> list[str]:
    missing = []
    for f in _REQUIRED_FIELDS.get(state.sector.value, []):
        v = state.get_value(f)
        if v is None or v == "" or v == []:
            missing.append(f)
    return missing


def _refusal(missing: list[str], state: AssessmentState) -> SolutionEstimate:
    zero = RangeEstimate(min=0, max=0, confidence="low", provenance=Provenance.ASSUMED,
                         source="refused: incomplete or unreliable state")
    return SolutionEstimate(
        recommended_pattern="", overall_automation=zero, performance=[],
        integration_complexity=state.integration_complexity or EffortBand.SMALL,
        engineering_effort=EffortBand.SMALL, engineering_hours=zero,
        needs_more_information=missing, assessment_confidence="low",
        confidence_notes=["estimate refused; no architecture selected"],
    )


def _llm_task_estimates(
    state: AssessmentState, architecture: str, caps: list[Capability],
) -> tuple[list[dict], list[str]]:
    """Ask for per-task automation AND handling time. Handling time is what
    lets code derive workload shares (C5) — the LLM never supplies a share."""
    from llm.openai_client import complete_json

    system = (
        "Estimate automation per workflow task for the given architecture.\n\n"
        "Return ONLY JSON: {\"tasks\": [{\"task\": str, \"capability\": str, "
        "\"automation_min\": float, \"automation_max\": float, "
        "\"handling_time_min_minutes\": float, \"handling_time_max_minutes\": float, "
        "\"confidence\": \"low|medium|high\", "
        "\"hitl\": \"autonomous|ai_assisted|human_review|human_only|escalation\", "
        "\"rationale\": str}]}\n\n"
        f"`capability` MUST be exactly one of: {[c.value for c in caps]}.\n"
        "`automation_*` is a percentage 0-100. `handling_time_*` is how many "
        "minutes a human currently spends on that task per unit — this is used "
        "to weight the tasks, so estimate it as carefully as you can.\n"
        "Do NOT return a workload share or percentage of total work. Do NOT "
        "cite benchmarks, studies or sources; `rationale` is your reasoning in "
        "plain words and is never treated as evidence.\n"
        "Give realistic ranges, not false precision."
    )
    user = (
        f"Sector: {state.sector.value}\nProblem: {state.problem}\n"
        f"Process: {state.process}\nRequired capabilities: {[c.value for c in caps]}\n"
        f"Selected architecture: {architecture}\n"
        f"Monthly volume: {point(state.monthly_volume)}\n"
        f"Average total handling time per unit: {point(state.avg_time_per_unit_minutes)} min"
    )
    result = complete_json(system, user)
    rows, dropped = [], []
    for t in result.get("tasks", []) or []:
        if not isinstance(t, dict):
            continue
        cap = caps_mod.parse_capability(t.get("capability", ""))
        if cap is None or cap not in caps:
            # C9: no substring rescue. A task we cannot tie to a capability is
            # reported, not silently deleted from the aggregate.
            dropped.append(f"{t.get('task', '?')} (capability {t.get('capability')!r})")
            continue
        rows.append({"row": t, "capability": cap})
    return rows, dropped


def _norm_conf(val) -> str:
    v = str(val or "").lower()
    return "high" if "high" in v else "low" if "low" in v else "medium"


def _norm_hitl(val) -> HitlMode:
    v = str(val or "").lower()
    if "human_review" in v or "human review" in v:
        return HitlMode.HUMAN_REVIEW
    if "escalat" in v:
        return HitlMode.ESCALATION
    if "human_only" in v or "human only" in v:
        return HitlMode.HUMAN_ONLY
    if "autonomous" in v:
        return HitlMode.AUTONOMOUS
    return HitlMode.AI_ASSISTED


def _float(row: dict, *keys) -> Optional[float]:
    for k in keys:
        v = row.get(k)
        if isinstance(v, (int, float)):
            return float(v)
    return None


def _build_tasks(
    rows: list[dict], state: AssessmentState, architecture: str,
) -> tuple[list[TaskAutomationEstimate], workload.WorkloadSplit, list[str]]:
    """Assemble tasks, deriving shares (C5) and anchoring automation (C4)."""
    times: list[Optional[RangeEstimate]] = []
    names: list[str] = []
    for r in rows:
        row = r["row"]
        lo = _float(row, "handling_time_min_minutes", "handling_time_minutes")
        hi = _float(row, "handling_time_max_minutes", "handling_time_minutes")
        names.append(str(row.get("task", "")) or r["capability"].value)
        if lo is None and hi is None:
            times.append(None)
        else:
            lo = lo if lo is not None else hi
            hi = hi if hi is not None else lo
            times.append(RangeEstimate(min=min(lo, hi), max=max(lo, hi),
                                       confidence="low", provenance=Provenance.ESTIMATED,
                                       source="llm_estimate: task handling time"))

    # N4 / D3: the user's observed aggregate is authoritative; the model's
    # decomposition supplies proportions only.
    split = workload.derive_shares(times, names,
                                   user_total_minutes=point(state.avg_time_per_unit_minutes))

    tasks, notes = [], []
    for r, share, name, time in zip(rows, split.shares, names, times):
        row, cap = r["row"], r["capability"]
        a_lo = _float(row, "automation_min", "min")
        a_hi = _float(row, "automation_max", "max")
        if a_lo is None or a_hi is None:
            notes.append(f"task {name!r} gave no automation range and was skipped")
            continue
        raw = RangeEstimate(min=min(a_lo, a_hi), max=max(a_lo, a_hi),
                            confidence=_norm_conf(row.get("confidence")),
                            provenance=Provenance.ESTIMATED, source="llm_estimate")
        anchored = evidence.anchor_automation(state.sector, cap, raw)
        if anchored.divergence_note:
            notes.append(f"{name}: {anchored.divergence_note}")
        tasks.append(TaskAutomationEstimate(
            task=name, capability=cap, architecture=architecture,
            # C1: LLM prose is kept as rationale, never as provenance.
            benchmark_basis=str(row.get("rationale", ""))[:400],
            estimate=anchored.estimate, hitl=_norm_hitl(row.get("hitl")),
            # Not rounded at rest: shares must sum to exactly 1 so a report
            # cannot show a breakdown that fails to add up. Round at display.
            workload_share=share,
            workload_share_provenance=split.provenance,
            handling_time_minutes=time,
            benchmark_anchor=anchored.anchor.citation if anchored.anchor else None,
            divergence_note=anchored.divergence_note,
        ))
    return tasks, split, notes


def _aggregate(tasks: list[TaskAutomationEstimate], split: workload.WorkloadSplit) -> RangeEstimate:
    """C6: workload-weighted interval bounds. An interval, not a distribution."""
    if not tasks:
        return RangeEstimate(min=0, max=0, confidence="low",
                             provenance=Provenance.DERIVED, source="no tasks")
    total = sum(t.workload_share for t in tasks) or 1.0
    lo = sum(t.estimate.min * t.workload_share for t in tasks) / total
    hi = sum(t.estimate.max * t.workload_share for t in tasks) / total
    return RangeEstimate(
        min=round(lo, 1), max=round(hi, 1),
        confidence="low" if split.provenance == Provenance.ASSUMED else "medium",
        provenance=Provenance.DERIVED,
        source=(f"workload-weighted interval over {len(tasks)} tasks "
                f"({split.basis}). Interval bounds, not a confidence interval."))


def _operating_cost_inputs(
    state: AssessmentState, tasks: list[TaskAutomationEstimate],
) -> OperatingCostInputs:
    """C12: expose the technical cost drivers the Economic Engine needs."""
    key = _INFERENCE_KEYS.get(state.sector.value)
    fig = benchmark_figure(state.sector, key) if key else None
    absent = []
    if fig is None:
        absent.append("per-unit inference price (no sourced figure for this sector)")
    absent.extend(["AI infrastructure", "monitoring", "other recurring costs"])
    review = ([t for t in tasks if t.hitl in (HitlMode.HUMAN_REVIEW, HitlMode.ESCALATION)]
              if tasks else [])
    return OperatingCostInputs(
        inference_price_per_unit=fig.as_range() if fig else None,
        inference_basis=fig.citation() if fig else "",
        human_review_share=(round(sum(t.workload_share for t in review), 4)
                            if tasks else None),
        absent_components=absent,
    )


def estimate(state: AssessmentState) -> SolutionEstimate:
    # --- 0. refuse on missing or unreliable critical inputs (C10) ---------
    missing = _missing_decision_critical(state)
    quality = confidence_mod.assess(state)
    if missing or quality.blocking:
        log.info("estimate refused: missing=%s unreliable=%s", missing, quality.blocking)
        return _refusal(missing + [f"unreliable: {b}" for b in quality.blocking], state)

    # --- 1. decompose and validate (C9, C8) ------------------------------
    validation = caps_mod.decompose(state)
    if not validation.valid or not validation.capabilities:
        return _refusal(["capability decomposition failed: " + "; ".join(validation.notes)],
                        state)
    caps = validation.capabilities

    # --- 2/3. registry candidates, ranking, HARD compliance filter -------
    outcome = rank_candidates(state, patterns_covering(set(caps)), set(caps))
    ranked = outcome.ranked

    if outcome.compliance_gap:
        # Section 8: do not force a recommendation. Return the gap with every
        # excluded candidate and its reason.
        gap = _refusal([outcome.compliance_statement], state)
        gap.compliance_gap = True
        gap.compliance_statement = outcome.compliance_statement
        gap.compliance_exclusions = [e.model_dump() for e in outcome.excluded]
        gap.confidence_notes = [
            "no architecture recommended: a hard compliance requirement could "
            "not be satisfied from implementation-specific evidence"]
        return gap

    if not ranked:
        return _refusal(
            [f"no registry pattern covers the required capabilities "
             f"{[c.value for c in caps]}"], state)
    top = ranked[0]
    chosen = next(i for i in top.pattern.implementations
                  if i.id == top.chosen_implementation)

    # --- 4. per-task estimates, derived shares, anchored automation ------
    rows, dropped = _llm_task_estimates(state, top.pattern.architecture, caps)
    tasks, split, task_notes = _build_tasks(rows, state, top.pattern.architecture)
    overall = _aggregate(tasks, split)
    divergences = [t.divergence_note for t in tasks if t.divergence_note]

    # --- 5. scope-derived bands (C2, C14) --------------------------------
    effort = scope.effort_scope(state, caps, [t.hitl for t in tasks],
                                implementation_kind=chosen.kind)
    integration = scope.integration_scope(state)
    hours = hours_for(effort.band)          # C3: hours and rate stay separate
    # Engineering cost uses IMPLEMENTATION labor for the assessment's geography;
    # None when no rate exists there, never a borrowed one.
    eng_cost = cost_for(effort.band, state.geography)

    integration_note = integration.explain()
    if state.integration_complexity and state.integration_complexity != integration.band:
        integration_note += (
            f". The interview recorded '{state.integration_complexity.value}'; the "
            f"derived band is used because integration complexity is an "
            f"engineering judgement about a system that does not exist yet.")

    # --- 6. performance, reference comparison ----------------------------
    perf = metrics_for(top.pattern.id, state.sector)
    reference = reference_for(state.sector)
    comparison = None
    if reference is not None:
        matched = top.pattern.id == reference.pattern
        comparison = ReferenceComparison(
            reference_id=reference.id, expected_pattern=reference.pattern,
            selected_pattern=top.pattern.id, match=matched,
            alignment=top.reference_alignment,
            deviation_reason=("" if matched else
                              f"selected {top.pattern.id} over the {reference.pattern} "
                              f"baseline: " + "; ".join(top.reasons)),
            active_deviations=top.active_deviations,
            unevaluated_conditions=top.unevaluated_conditions,
        )

    # --- 7. structured risk controls (C13) -------------------------------
    controls = risks.controls_for(
        capabilities=caps, hitl_modes=[t.hitl for t in tasks],
        integrations=len([t for t in (state.current_tools or []) if str(t).strip()]),
        compliance_gap=any("compliance" in r.lower() for r in top.risks),
        scale_shortfall=any("not rated" in r.lower() for r in top.risks),
        implementation=chosen,
    )

    # --- 8. provenance integrity sweep (C1) ------------------------------
    warnings: list[str] = []
    for t in tasks:
        t.estimate, w = evidence.enforce_provenance(t.estimate, f"task[{t.task}]")
        if w:
            warnings.append(w)
    for pm in perf:
        pm.estimate, w = evidence.enforce_provenance(pm.estimate, f"performance[{pm.metric}]")
        if w:
            warnings.append(w)

    # --- 9. weighted confidence (N9, N10) --------------------------------
    penalties = []
    if split.provenance == Provenance.ASSUMED:
        penalties.append((split.warning or "workload shares are an assumed equal split", 0.15))
    if split.reconciliation.severity.value == "large":
        penalties.append((split.reconciliation.statement, 0.15))
    elif split.reconciliation.severity.value == "moderate":
        penalties.append((split.reconciliation.statement, 0.07))
    if validation.missing_vs_reference:
        penalties.append(("capability decomposition differs from the sector baseline", 0.10))
    if dropped:
        penalties.append((f"{len(dropped)} task(s) could not be tied to a capability", 0.10))
    if divergences:
        penalties.append(("an automation claim materially exceeds benchmark evidence", 0.10))

    conf = confidence_mod.assess(
        state, estimate_ranges=[t.estimate for t in tasks], extra_penalties=penalties)
    confidence, conf_notes = conf.level, list(conf.notes)
    if conf.floor_applied:
        conf_notes.append(conf.floor_applied)

    needs_more = []
    if split.reconciliation.blocks_estimate:
        return _refusal(
            [f"handling-time reconciliation: {split.reconciliation.statement}"], state)
    if dropped:
        needs_more.append(f"tasks not tied to a capability: {dropped}")
    if validation.unparsed:
        needs_more.append(f"unparsable capabilities: {validation.unparsed}")

    return SolutionEstimate(
        recommended_pattern=top.pattern.id,
        recommended_implementation=top.chosen_implementation,
        candidate_implementations=[r.pattern.id for r in ranked],
        task_automation=tasks, overall_automation=overall, performance=perf,
        reference_comparison=comparison,
        integration_complexity=integration.band,
        integration_complexity_reported=state.integration_complexity,
        integration_basis=integration_note,
        engineering_effort=effort.band, engineering_hours=hours,
        engineering_cost=eng_cost, effort_basis=effort.explain(),
        hitl_requirements={t.task: t.hitl for t in tasks},
        risk_controls=[c.model_dump() for c in controls],
        risks_and_mitigations=[{"risk": c.risk, "mitigation": "; ".join(c.controls)}
                               for c in controls],
        key_uncertainties=(list(top.reasons) + task_notes + validation.notes +
                           [f"not evaluated from the assessment: {c}"
                            for c in top.unevaluated_conditions] +
                           [f"scope factor not captured: {u}"
                            for u in effort.unknown_factors]),
        fit_explanations=[f"{top.pattern.name} via {chosen.name}"] + list(top.reasons),
        capability_validation=validation.model_dump(mode="json"),
        compliance_exclusions=[e.model_dump() for e in outcome.excluded],
        compliance_verdicts=[
            v.model_dump(mode="json") for v in compliance_verdicts(
                top.chosen_implementation,
                list(state.risk.compliance_exposure or [])).values()],
        operating_cost_inputs=_operating_cost_inputs(state, tasks),
        assessment_confidence=confidence, confidence_notes=conf_notes,
        confidence_score=conf.score,
        time_reconciliation=split.reconciliation.model_dump(mode="json"),
        provenance_warnings=warnings, needs_more_information=needs_more,
    )
