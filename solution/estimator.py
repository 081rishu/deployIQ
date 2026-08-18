"""AI Solution & Architecture Estimator — orchestrator.

Boundaries:
  LLM: decompose (capabilities), explain fit, per-task automation estimates.
  Registry: constrains candidates (patterns.py).
  Deterministic: generate candidates, filter/rank (ranking.py), effort bands
                (effort_bands.py), aggregation.
  Reference solutions: baseline for comparison (reference_solutions.py).
"""

from __future__ import annotations

from schemas.assessment_state import AssessmentState, EffortBand
from solution import capabilities as caps_mod
from solution.effort_bands import hours_for
from solution.patterns import patterns_covering
from solution.performance import metrics_for
from solution.ranking import filter_and_rank
from solution.reference_solutions import for_sector
from solution.schema import (
    Provenance,
    RangeEstimate,
    SolutionEstimate,
    TaskAutomationEstimate,
)


# Minimal decision-critical fields per sector. If any is missing, the
# estimator refuses to produce a confident estimate (P0.1). Optional fields
# may stay unknown.
_REQUIRED_FIELDS = {
    "customer_support": ["process", "monthly_volume", "current_headcount",
                         "avg_time_per_unit_minutes", "required_accuracy",
                         "integration_complexity"],
    "document_processing": ["process", "monthly_volume",
                             "avg_time_per_unit_minutes", "required_accuracy",
                             "integration_complexity"],
}


def _missing_decision_critical(state: AssessmentState) -> list[str]:
    """Return the sector's decision-critical fields that are missing/uncertain."""
    fields = _REQUIRED_FIELDS.get(state.sector.value, [])
    missing = []
    for f in fields:
        v = state.get_value(f)
        if v is None or v == "" or v == []:
            missing.append(f)
    return missing


def _llm_task_estimates(state, architecture: str, caps: list) -> list[TaskAutomationEstimate]:
    """LLM estimates automation per task, tied to the selected architecture.
    Every value is a range + confidence; never a bare number."""
    from llm.openai_client import complete_json
    import json

    system = (
        "Estimate automation per workflow task for the given architecture. "
        "Return ONLY JSON: {'tasks': [{'task': str, 'capability': str, "
        "'min': float, 'max': float, 'confidence': 'low|medium|high', "
        "'hitl': 'autonomous|ai_assisted|human_review|human_only|escalation', "
        "'benchmark_basis': str}]}. "
        "Automation is a percentage 0-100. Give realistic ranges, not false precision."
    )
    user = (
        f"Sector: {state.sector.value}\nProblem: {state.problem}\n"
        f"Process: {state.process}\nRequired capabilities: {[c.value for c in caps]}\n"
        f"Selected architecture: {architecture}\n"
        f"Scale: monthly_volume={state.monthly_volume}\n"
        f"Integration complexity: {state.integration_complexity}"
    )
    result = complete_json(system, user)
    tasks = []
    for t in result.get("tasks", []):
        try:
            est = t.get("estimate") or {}
            lo = float(t.get("min", est.get("min", t.get("lo", est.get("lo")))))
            hi = float(t.get("max", est.get("max", t.get("hi", est.get("hi")))))
            if lo > hi:
                lo, hi = hi, lo
            cap = caps_mod_decorator(caps, t.get("capability", ""))
            if cap is None:
                continue  # capability not recognized -> cannot tie estimate to architecture
            share = float(t.get("workload_share", t.get("share", est.get("share", 1.0))))
            tasks.append(TaskAutomationEstimate(
                task=t.get("task", ""), capability=cap,
                architecture=architecture, benchmark_basis=t.get("benchmark_basis", ""),
                estimate=RangeEstimate(
                    min=lo, max=hi,
                    confidence=_norm_conf(t.get("confidence", "medium")),
                    provenance=Provenance.LLM_ESTIMATE,
                    source=t.get("benchmark_basis", "") or "llm_estimate",
                ),
                hitl=_norm_hitl(t.get("hitl", "ai_assisted")),
                workload_share=share,
            ))
        except (KeyError, ValueError, TypeError):
            continue
    return tasks


def _norm_conf(val) -> str:
    v = str(val or "").lower()
    if "high" in v:
        return "high"
    if "low" in v:
        return "low"
    return "medium"


def _norm_hitl(val) -> str:
    v = str(val or "").lower()
    if "human review" in v or "human_review" in v:
        return "human_review"
    if "escalat" in v:
        return "escalation"
    if "optional" in v or "recommended" in v or "assist" in v:
        return "ai_assisted"
    if "human only" in v or "human_only" in v:
        return "human_only"
    return "ai_assisted"


def caps_mod_decorator(caps, val):
    from solution.schema import Capability
    v = str(val or "").lower()
    for c in caps:
        if c.value == v:
            return c
    # Keyword match against capability descriptions (LLM returns prose, not enums).
    keywords = {
        Capability.CLASSIFY: ["classif"],
        Capability.EXTRACT: ["extract", "ocr", "parse"],
        Capability.GENERATE: ["generat", "reply", "response", "draft"],
        Capability.SEARCH_RETRIEVE: ["retriev", "search", "knowledge"],
        Capability.ROUTE: ["route", "triage", "assign"],
        Capability.HUMAN_ESCALATE: ["escalat", "complex"],
        Capability.HUMAN_REVIEW: ["review"],
        Capability.INGEST: ["ingest", "intake", "capture"],
        Capability.POST_PROCESS: ["post", "follow", "update"],
        Capability.VALIDATE: ["valid", "check", "approve"],
    }
    for cap, kws in keywords.items():
        if cap in caps and any(k in v for k in kws):
            return cap
    return None


def _aggregate(tasks: list[TaskAutomationEstimate]) -> RangeEstimate:
    """Workload-weighted mean of task automation ranges (P1 fix).
    A task representing 80% of workload matters more than one at 5%."""
    if not tasks:
        return RangeEstimate(min=0, max=0, confidence="low",
                             provenance=Provenance.DERIVED, source="no tasks")
    total = sum(max(t.workload_share, 0.0) for t in tasks) or 1.0
    lo = sum(t.estimate.min * max(t.workload_share, 0.0) for t in tasks) / total
    hi = sum(t.estimate.max * max(t.workload_share, 0.0) for t in tasks) / total
    return RangeEstimate(
        min=round(lo, 1), max=round(hi, 1), confidence="medium",
        provenance=Provenance.DERIVED,
        source=f"workload-weighted mean over {len(tasks)} tasks",
    )


def estimate(state: AssessmentState) -> SolutionEstimate:
    # 0. Refuse on missing decision-critical info (P0.1). Optional fields may
    #    stay unknown; we only block when a missing field prevents a
    #    defensible estimate.
    missing = _missing_decision_critical(state)
    if missing:
        return SolutionEstimate(
            recommended_pattern="",
            overall_automation=RangeEstimate(min=0, max=0, confidence="low",
                                             provenance=Provenance.ASSUMPTION,
                                             source="refused: incomplete state"),
            performance=[],
            integration_complexity=state.integration_complexity or EffortBand.SMALL,
            engineering_effort="small",
            engineering_hours=RangeEstimate(min=0, max=0, confidence="low",
                                            provenance=Provenance.ASSUMPTION,
                                            source="refused: incomplete state"),
            needs_more_information=missing,
        )

    # 1. LLM decomposes workflow -> capabilities.
    caps = caps_mod.decompose(state)

    # 2. Registry deterministically generates compatible patterns.
    candidates = patterns_covering(set(caps))

    # 3. Deterministic filter + rank.
    ranked = filter_and_rank(state, candidates, set(caps))
    if not ranked:
        raise RuntimeError("no candidate solution pattern covers the required capabilities")
    top = ranked[0]

    # 4. Effort band -> hours (deterministic).
    effort = top.pattern.implementations
    chosen = next((i for i in effort if i.id == top.chosen_implementation), effort[0])
    band = chosen.compatibility.technical_complexity
    hours = hours_for(band)

    # 5. LLM per-task automation estimates tied to the architecture.
    tasks = _llm_task_estimates(state, top.pattern.architecture, caps)
    overall = _aggregate(tasks)

    # 6. Reference baseline + task-specific performance metrics (P0.1).
    refs = for_sector(state.sector)
    rationale = refs[0].recommended_architecture if refs else ""
    perf = metrics_for(top.pattern.id)

    return SolutionEstimate(
        recommended_pattern=top.pattern.id,
        candidate_implementations=[r.pattern.id for r in ranked],
        task_automation=tasks,
        overall_automation=overall,
        performance=perf,
        integration_complexity=state.integration_complexity,
        engineering_effort=band,
        engineering_hours=hours,
        hitl_requirements={t.task: t.hitl for t in tasks},
        risks_and_mitigations=[{"risk": r, "mitigation": "add guardrails + review"} for r in top.risks],
        key_uncertainties=top.reasons,
        fit_explanations=[f"{top.pattern.name}: baseline {rationale}"],
    )
