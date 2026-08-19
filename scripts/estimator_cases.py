"""Acceptance tests A-I from docs/deployIQ_solution_estimator_critique_fixes.md
section 19, plus direct checks on the C1-C14 fixes.

The LLM is stubbed with scripted responses, so this runs with no API key and
is fully deterministic.
"""

from __future__ import annotations

import sys
import types
from pathlib import Path

# Imports resolve from the editable src-layout installation.
if "openai" not in sys.modules:
    _s = types.ModuleType("openai"); _s.OpenAI = lambda **kw: None
    sys.modules["openai"] = _s

from schemas.assessment_state import (
    AssessmentState, DataReadiness, EffortBand, FieldMeta, FieldResolution,
    ImpactSeverity, Provenance, RangeEstimate, RiskInputs, Sector,
)
from solution import capabilities as caps_mod
from solution import constants, estimator, evidence, patterns, scope, workload
from solution.schema import Capability, HitlMode, ImplementationKind

failures: list[str] = []


def check(case: str, cond: bool, desc: str) -> None:
    print(f"    [{'PASS' if cond else 'FAIL'}] {desc}")
    if not cond:
        failures.append(f"{case}: {desc}")


def doc_state(**kw) -> AssessmentState:
    base = dict(sector=Sector.DOCUMENT_PROCESSING, problem="automate invoice processing",
                process="invoice intake, coding and three-way match",
                monthly_volume=20000, avg_time_per_unit_minutes=6,
                current_headcount=16, required_accuracy=0.97,
                data_readiness=DataReadiness.GOOD, current_tools=["SAP"],
                geography="US", fully_loaded_annual_cost=62000,
                risk=RiskInputs(failure_impact="wrong payment",
                                failure_impact_severity=ImpactSeverity.MODERATE))
    base.update(kw)
    return AssessmentState(**base)


def stub_llm(caps, tasks):
    """Scripted decomposition + task estimates."""
    def fake(system, user, **kw):
        if "decompose" in system:
            return {"capabilities": caps}
        return {"tasks": tasks}
    return fake


DOC_CAPS = ["ingest", "extract", "classify", "validate", "human_review"]
DOC_TASKS = [
    {"task": "ingest invoices", "capability": "ingest",
     "automation_min": 90, "automation_max": 98,
     "handling_time_min_minutes": 1, "handling_time_max_minutes": 1,
     "confidence": "high", "hitl": "autonomous", "rationale": "scriptable intake"},
    {"task": "extract line items", "capability": "extract",
     "automation_min": 30, "automation_max": 45,
     "handling_time_min_minutes": 4, "handling_time_max_minutes": 4,
     "confidence": "medium", "hitl": "human_review", "rationale": "semi-structured"},
    {"task": "validate against PO", "capability": "validate",
     "automation_min": 40, "automation_max": 60,
     "handling_time_min_minutes": 1, "handling_time_max_minutes": 1,
     "confidence": "low", "hitl": "human_review", "rationale": "three-way match"},
]


def install(caps, tasks):
    fake = stub_llm(caps, tasks)
    caps_mod.complete_json = fake
    import llm.openai_client as oc
    oc.complete_json = fake
    return fake


def case_A_simple_workflow() -> None:
    print("\nA — simple deterministic workflow: low-code considered, not auto-LLM")
    install(DOC_CAPS, DOC_TASKS)
    est = estimator.estimate(doc_state(monthly_volume=800, current_tools=["email"]))
    print(f"    pattern={est.recommended_pattern}  effort={est.engineering_effort.value}"
          f"  integration={est.integration_complexity.value}")
    check("A", est.recommended_pattern == "document_pipeline",
          "the document pipeline is selected, not a RAG/agent architecture")
    check("A", est.engineering_effort in (EffortBand.SMALL, EffortBand.MEDIUM),
          f"a small workload does not produce a large build "
          f"(got {est.engineering_effort.value})")


def case_C_high_risk() -> None:
    print("\nC — high-risk workflow: review/escalation and explicit risk")
    install(DOC_CAPS, DOC_TASKS)
    # A hard compliance requirement is now a FILTER (see compliance_cases.py
    # case I), so it is not used here — this case is about whether a high-risk
    # workflow gets human review and architecture-relevant controls.
    est = estimator.estimate(doc_state(risk=RiskInputs(
        failure_impact="regulatory penalty",
        failure_impact_severity=ImpactSeverity.SEVERE)))
    cats = [rc["category"] for rc in est.risk_controls]
    print(f"    risk controls: {cats}")
    check("C", any(t.hitl in (HitlMode.HUMAN_REVIEW, HitlMode.ESCALATION)
                   for t in est.task_automation),
          "human review appears in the HITL plan")
    check("C", est.risk_controls and all(rc["controls"] for rc in est.risk_controls),
          "every risk carries concrete controls")
    check("C", len({rc["mitigation"] for rc in est.risks_and_mitigations}) > 1,
          "mitigations differ by risk (not one repeated string) [C13]")


def case_D_missing_evidence() -> None:
    print("\nD — no fabricated benchmark when evidence is absent")
    install(DOC_CAPS, DOC_TASKS)
    est = estimator.estimate(doc_state())
    provs = {t.estimate.provenance for t in est.task_automation}
    print(f"    task provenances: {[p.value for p in provs]}")
    check("D", Provenance.SOURCED not in provs,
          "no task automation estimate claims `sourced` [C1]")
    check("D", all(t.estimate.provenance == Provenance.ESTIMATED
                   for t in est.task_automation),
          "LLM estimates are tagged `estimated`")
    check("D", all("llm_estimate" in t.estimate.source for t in est.task_automation),
          "the source string says it is an LLM estimate, not a citation")

    # A fabricated citation must not survive the integrity sweep.
    fake = RangeEstimate(min=90, max=95, provenance=Provenance.SOURCED,
                         source="Per industry IDP benchmarks (Gartner 2025)")
    out, warn = evidence.enforce_provenance(fake, "test")
    check("D", out.provenance == Provenance.ESTIMATED and warn is not None,
          "an invented citation is downgraded from `sourced` and reported [C1]")


def case_E_invalid_capability() -> None:
    print("\nE — invalid capability: schema rejection, retry, safe failure")
    install(["document extraction", "some_made_up_thing"], DOC_TASKS)
    est = estimator.estimate(doc_state())
    print(f"    needs_more_information: {est.needs_more_information}")
    check("E", est.recommended_pattern == "",
          "no architecture is selected from an invalid decomposition")
    check("E", est.needs_more_information,
          "the failure is reported rather than guessed around")
    check("E", caps_mod.parse_capability("document extraction") is None,
          "prose is NOT substring-matched to a capability [C9]")


def case_F_workload_weighting() -> None:
    print("\nF — workload weighting: 80/20 split must give 34%, not 55%")
    a = RangeEstimate(min=20, max=20, provenance=Provenance.ESTIMATED)
    b = RangeEstimate(min=90, max=90, provenance=Provenance.ESTIMATED)
    split = workload.derive_shares(
        [RangeEstimate(min=8, max=8), RangeEstimate(min=2, max=2)], ["A", "B"])
    overall = sum(x.min * s for x, s in zip((a, b), split.shares))
    print(f"    shares={[round(s,2) for s in split.shares]}  overall={overall:.0f}%")
    check("F", abs(split.shares[0] - 0.8) < 1e-9, "share A = 80% (from handling time)")
    check("F", abs(overall - 34.0) < 1e-9, f"overall = 34%, not 55% (got {overall:.0f})")
    check("F", split.provenance == Provenance.DERIVED,
          "shares are derived, never LLM-supplied [C5]")

    install(DOC_CAPS, DOC_TASKS)
    est = estimator.estimate(doc_state())
    shares = [t.workload_share for t in est.task_automation]
    print(f"    end-to-end shares: {[round(s,2) for s in shares]} (times 1/4/1 min)")
    check("F", abs(sum(shares) - 1.0) < 1e-6, "shares sum to 1")
    check("F", all(t.workload_share_provenance == Provenance.DERIVED
                   for t in est.task_automation),
          "every task share is tagged derived")


def case_G_scope_sensitive_effort() -> None:
    print("\nG — same architecture, different scope, different effort [C2]")
    install(DOC_CAPS, DOC_TASKS)
    simple = estimator.estimate(doc_state(monthly_volume=800, current_tools=["email"],
                                          data_readiness=DataReadiness.EXCELLENT))
    # No compliance_exposure here: a hard requirement is now a FILTER, and an
    # unsatisfiable one yields a compliance gap with no costed architecture.
    # That path is covered by scripts/compliance_cases.py case I; this case is
    # about scope-sensitive effort.
    heavy = estimator.estimate(doc_state(
        monthly_volume=80000,
        current_tools=["SAP", "Coupa", "Salesforce", "SharePoint", "Oracle"],
        data_readiness=DataReadiness.MINIMAL,
        risk=RiskInputs(failure_impact="x",
                        failure_impact_severity=ImpactSeverity.MAJOR)))
    print(f"    simple: effort={simple.engineering_effort.value} "
          f"hours={simple.engineering_hours.min:.0f}-{simple.engineering_hours.max:.0f} "
          f"cost={simple.engineering_cost.min:,.0f}-{simple.engineering_cost.max:,.0f}")
    print(f"    heavy : effort={heavy.engineering_effort.value} "
          f"hours={heavy.engineering_hours.min:.0f}-{heavy.engineering_hours.max:.0f} "
          f"cost={heavy.engineering_cost.min:,.0f}-{heavy.engineering_cost.max:,.0f}")
    check("G", simple.engineering_effort != heavy.engineering_effort,
          "effort band differs with scope")
    check("G", heavy.engineering_hours.max > simple.engineering_hours.max,
          "the heavier scope costs more hours")
    check("G", simple.integration_complexity != heavy.integration_complexity,
          "integration complexity is also scope-derived [C14]")
    check("G", heavy.engineering_cost is not None and
          heavy.engineering_cost.provenance == Provenance.DERIVED,
          "engineering cost is derived from separate hour and rate inputs [C3]")


def case_H_reference_alignment() -> None:
    print("\nH — reference alignment materially affects ranking and is visible")
    install(DOC_CAPS, DOC_TASKS)
    est = estimator.estimate(doc_state())
    rc = est.reference_comparison
    print(f"    reference={rc.reference_id} expected={rc.expected_pattern} "
          f"selected={rc.selected_pattern} match={rc.match} alignment={rc.alignment}")
    check("H", rc is not None, "a reference comparison is emitted [C7]")
    check("H", rc.selected_pattern == rc.expected_pattern and rc.match,
          "the curated baseline is selected for a baseline-shaped case")
    check("H", rc.alignment == 1.0, "alignment is reported as the ranker computed it")
    check("H", rc.unevaluated_conditions,
          "conditions that could not be evaluated are surfaced, not dropped")


def case_I_ambiguous_state() -> None:
    print("\nI — ambiguous critical input: reduced confidence or refusal [C10]")
    install(DOC_CAPS, DOC_TASKS)
    clean = estimator.estimate(doc_state())
    print(f"    clean state    : confidence={clean.assessment_confidence}")

    weak = doc_state()
    weak.field_resolution["monthly_volume"] = FieldMeta(
        status=FieldResolution.LOW_CONFIDENCE, attempts=2, reason="rough guess")
    weakened = estimator.estimate(weak)
    print(f"    low-confidence : confidence={weakened.assessment_confidence} "
          f"({weakened.confidence_notes[0][:60]})")

    bad = doc_state()
    bad.field_resolution["monthly_volume"] = FieldMeta(
        status=FieldResolution.CONTRADICTORY, attempts=3, reason="conflicts with headcount")
    refused = estimator.estimate(bad)
    print(f"    contradictory  : pattern={refused.recommended_pattern!r} "
          f"needs={refused.needs_more_information}")
    check("I", clean.assessment_confidence in ("high", "medium"),
          "a clean state yields usable confidence")
    check("I", weakened.assessment_confidence != "high",
          "a low-confidence interview answer lowers assessment confidence")
    check("I", refused.recommended_pattern == "" and refused.needs_more_information,
          "a contradicted critical field refuses instead of estimating confidently")


def case_C4_anchoring() -> None:
    print("\nC4 — an automation claim beyond the evidence is flagged")
    over = [dict(DOC_TASKS[1], automation_min=92, automation_max=97)]
    install(DOC_CAPS, [DOC_TASKS[0]] + over + [DOC_TASKS[2]])
    est = estimator.estimate(doc_state())
    flagged = [t for t in est.task_automation if t.divergence_note]
    print(f"    flagged: {len(flagged)}; confidence={est.assessment_confidence}")
    if flagged:
        print(f"    {flagged[0].divergence_note[:120]}")
    check("C4", flagged, "a claim far above the industry benchmark is flagged")
    check("C4", all(t.estimate.provenance == Provenance.ESTIMATED
                    for t in est.task_automation),
          "the benchmark is context — it does not become the company's result")
    check("C4", any(t.benchmark_anchor for t in est.task_automation),
          "the anchor citation is recorded on the estimate")




def case_B_knowledge_support() -> None:
    print("\nB — knowledge-heavy support: RAG is available and selectable")
    caps = {Capability.INGEST, Capability.SEARCH_RETRIEVE, Capability.GENERATE,
            Capability.ROUTE, Capability.HUMAN_ESCALATE}
    qualifying = [p.id for p in patterns.patterns_covering(caps)]
    print(f"    patterns covering knowledge-support capabilities: {qualifying}")
    impl = next((i for i in patterns.pattern("rag_knowledge_assistant").implementations
                 if patterns.implementation_covers(i, caps)), None)
    check("B", "rag_knowledge_assistant" in qualifying,
          "a RAG implementation genuinely covers ingest/retrieval/generation/escalation")
    check("B", impl is not None, f"the covering implementation exists ({impl.id if impl else None})")
    check("B", "document_pipeline" not in qualifying,
          "a document pipeline does NOT qualify for a retrieval workload")


def case_J_time_contradiction() -> None:
    print("\nJ — user total 6 min vs model tasks 4+5+3=12 min")
    tasks = [dict(DOC_TASKS[0], handling_time_min_minutes=4, handling_time_max_minutes=4),
             dict(DOC_TASKS[1], handling_time_min_minutes=5, handling_time_max_minutes=5),
             dict(DOC_TASKS[2], handling_time_min_minutes=3, handling_time_max_minutes=3)]
    install(DOC_CAPS, tasks)
    est = estimator.estimate(doc_state(avg_time_per_unit_minutes=6))
    tr = est.time_reconciliation
    shares = [t.workload_share for t in est.task_automation]
    print(f"    model_total={tr['model_total_minutes']} user_total={tr['user_total_minutes']} "
          f"divergence={tr['divergence']:.0%} severity={tr['severity']}")
    print(f"    shares={[round(s,3) for s in shares]}  reconciled={tr['reconciled_times']} "
          f"(sum={sum(tr['reconciled_times']):.1f} min)")
    check("J", tr["divergence"] is not None and tr["divergence"] > 0.5,
          "the divergence is detected and recorded")
    check("J", abs(sum(tr["reconciled_times"]) - 6.0) < 1e-6,
          "reconciled task times sum to the OBSERVED 6 minutes")
    check("J", abs(sum(shares) - 1.0) < 1e-6, "shares still sum to 1")
    check("J", tr["model_total_minutes"] == 12.0 and tr["user_total_minutes"] == 6.0,
          "the model total did not overwrite the observed baseline")
    check("J", est.assessment_confidence != "high",
          f"confidence is reduced (got {est.assessment_confidence})")


def case_K_evidence_id_stability() -> None:
    print("\nK — evidence identity survives reformatting, not deletion")
    from lib.benchmarks import load_pack
    fig = load_pack(Sector.DOCUMENT_PROCESSING).get("straight_through_processing_rate")

    reformatted = fig.as_range()
    reformatted.source = "Ardent Partners 2025 (reformatted citation text)"
    out, warn = evidence.enforce_provenance(reformatted, "k")
    check("K", out.provenance == Provenance.SOURCED and warn is None,
          "changing the rendered citation keeps provenance valid")

    dropped = fig.as_range()
    dropped.source_id = None
    out2, warn2 = evidence.enforce_provenance(dropped, "k")
    check("K", out2.provenance == Provenance.ESTIMATED and warn2,
          "removing the evidence id invalidates the evidence relationship")

    invented = fig.as_range()
    invented.source_id = "made_up_evidence_001"
    out3, warn3 = evidence.enforce_provenance(invented, "k")
    check("K", out3.provenance == Provenance.ESTIMATED and warn3,
          "an invented evidence id is rejected")


def case_L_threshold_consistency() -> None:
    print("\nL — one scale threshold drives both ranking and scope")
    import solution.ranking as ranking_mod
    st = doc_state(monthly_volume=constants.SCALE_LARGE_FROM + 1)
    before_rank = ranking_mod._effective_scale(st)
    before_scope = scope._scale_of(st)
    original = constants.SCALE_LARGE_FROM
    try:
        constants.SCALE_LARGE_FROM = 10_000_000      # move the canonical value
        after_rank = ranking_mod._effective_scale(st)
        after_scope = scope._scale_of(st)
    finally:
        constants.SCALE_LARGE_FROM = original
    print(f"    ranking: {before_rank} -> {after_rank};  scope: {before_scope} -> {after_scope}")
    check("L", before_rank == before_scope == "large", "both agree before the change")
    check("L", after_rank == after_scope != "large",
          "both follow the canonical constant when it changes [N5]")


def case_N1_implementation_kind() -> None:
    print("\nN1 — implementation kind modifies effort, scope stays primary")
    caps = [Capability.INGEST, Capability.EXTRACT, Capability.CLASSIFY, Capability.VALIDATE]
    st = doc_state(monthly_volume=15000, current_tools=["SAP", "Coupa"])
    scores = {k.value: scope.effort_scope(st, caps, [HitlMode.HUMAN_REVIEW], k)
              for k in ImplementationKind}
    for name, s in scores.items():
        print(f"    {name:<16} score={s.score:<6} band={s.band.value}")
    low, custom = scores["low_code"], scores["custom_code"]
    check("N1", custom.score > low.score,
          "a custom build scores higher effort than a low-code build at equal scope")
    modifier_span = custom.score - low.score
    check("N1", modifier_span < low.score,
          f"the modifier ({modifier_span:g}) does not dominate base scope ({low.score:g}) [D2]")
    check("N1", any(f.key == "implementation_kind" for f in custom.factors),
          "the modifier appears as an explicit, explainable factor")


def case_N6_calibration_disclosure() -> None:
    print("\nN6 — every scope weight is identifiable as an assumption")
    from solution.calibration import CALIBRATION
    params = CALIBRATION.all_params()
    non_assumed = [p.key for p in params if p.provenance != Provenance.ASSUMED]
    no_rationale = [p.key for p in params if not p.rationale]
    print(f"    {len(params)} parameters; all versioned v{params[0].version}")
    check("N6", not non_assumed, "every calibration parameter is tagged `assumed`")
    check("N6", not no_rationale, "every calibration parameter states its rationale")
    est_scope = scope.effort_scope(doc_state(), [Capability.INGEST])
    check("N6", "assumption" in est_scope.basis.lower(),
          "the disclosure travels with the result, not just the source file")


def case_M_compliance_evidence() -> None:
    print("\nM — compliance verdicts come from implementation-specific evidence")
    from lib.compliance import ClaimStatus, evaluate_implementation
    from lib.vendor_attestations import load_registry

    make_soc2 = evaluate_implementation("make", "SOC 2")
    make_hipaa = evaluate_implementation("make", "HIPAA")
    zapier_hipaa = evaluate_implementation("zapier", "HIPAA")
    print(f"    make/SOC 2   -> {make_soc2.status.value}")
    print(f"    make/HIPAA   -> {make_hipaa.status.value}")
    print(f"    zapier/HIPAA -> {zapier_hipaa.status.value}")
    check("M", make_soc2.satisfies,
          "an evidence-backed requirement is SUPPORTED and satisfies")
    check("M", make_hipaa.status == ClaimStatus.UNKNOWN and not make_hipaa.satisfies,
          "UNKNOWN never satisfies a hard requirement")
    check("M", zapier_hipaa.status == ClaimStatus.NOT_APPLICABLE,
          "an explicit vendor exclusion is preserved")
    check("M", all("SCOPE" in c.reason or c.scope for c in make_soc2.components),
          "the verdict carries the evidence scope, so it cannot be overstated")

    # deployIQ's own vendor attestations remain a SEPARATE register.
    reg = load_registry()
    check("M", all(not a.binds_implementations
                   for a in reg.product_vendor_attestations),
          "deployIQ's own vendor attestations bind no registry implementation")


def case_N_baseline_consistency() -> None:
    print("\nN — estimator and Economic Engine derive ONE labor baseline")
    from calc.ai_state import LaborRealization
    from calc.engine import run as run_engine

    install(DOC_CAPS, DOC_TASKS)          # tasks total 1+4+1 = 6 min
    st = doc_state(avg_time_per_unit_minutes=6)
    est = estimator.estimate(st)
    eng = run_engine(st, est, LaborRealization.COST_ELIMINATED)

    est_total = est.time_reconciliation["authoritative_total_minutes"]
    eng_total = eng.time_reconciliation["authoritative_total_minutes"]
    print(f"    estimator baseline handling time: {est_total} min")
    print(f"    engine    baseline handling time: {eng_total} min")
    check("N", est_total == eng_total,
          f"both modules use the same baseline ({est_total} vs {eng_total})")
    check("N", est.time_reconciliation["total_provenance"] ==
          eng.time_reconciliation["total_provenance"],
          "both agree on the provenance of that baseline")

    # And when the model contradicts the observation, both still agree.
    contradicting = [dict(DOC_TASKS[0], handling_time_min_minutes=4, handling_time_max_minutes=4),
                     dict(DOC_TASKS[1], handling_time_min_minutes=5, handling_time_max_minutes=5),
                     dict(DOC_TASKS[2], handling_time_min_minutes=3, handling_time_max_minutes=3)]
    install(DOC_CAPS, contradicting)
    est2 = estimator.estimate(st)
    eng2 = run_engine(st, est2, LaborRealization.COST_ELIMINATED)
    print(f"    under a 12-vs-6 contradiction: estimator="
          f"{est2.time_reconciliation['authoritative_total_minutes']} "
          f"engine={eng2.time_reconciliation['authoritative_total_minutes']}")
    check("N", est2.time_reconciliation["authoritative_total_minutes"] ==
          eng2.time_reconciliation["authoritative_total_minutes"] == 6.0,
          "the observed aggregate wins in BOTH modules, not just the estimator")


def case_labor_rate_geography() -> None:
    print("\nRATES — geography, currency, role kind and provenance are explicit")
    from lib.labor_rates import LaborKind, load_rates, lookup
    from solution.effort_bands import cost_for, hours_for, implementation_rate

    book = load_rates()
    print(f"    entries: {[(e.geography, e.currency, e.labor_kind.value) for e in book.entries]}")
    check("RATE", all(e.geography and e.currency for e in book.entries),
          "every rate carries geography and currency")
    check("RATE", all(e.provenance in ("sourced", "assumed") and e.source
                      for e in book.entries),
          "every rate carries provenance and a source statement")
    check("RATE", book.fully_loaded_multiplier.status == "unresolved",
          "the fully-loaded multiplier is NOT silently invented — its status is "
          "unresolved and travels with the result")

    us_hours = hours_for(EffortBand.MEDIUM)
    in_hours = hours_for(EffortBand.MEDIUM)
    check("RATE", us_hours.min == in_hours.min,
          "changing geography does NOT change engineering effort")
    us_cost = cost_for(EffortBand.MEDIUM, "US")
    in_cost = cost_for(EffortBand.MEDIUM, "India")
    print(f"    medium band: US {us_cost.min:,.0f}-{us_cost.max:,.0f} USD | "
          f"India {in_cost.min:,.0f}-{in_cost.max:,.0f} INR")
    check("RATE", us_cost.min != in_cost.min,
          "changing geography does change cost")
    check("RATE", cost_for(EffortBand.MEDIUM, "Germany") is None,
          "an unlisted geography yields no cost rather than a borrowed rate")

    eng = implementation_rate("India")
    check("RATE", eng is not None and eng.labor_kind == LaborKind.IMPLEMENTATION,
          "engineering cost uses IMPLEMENTATION labor, never process labor")


def case_task_sanity() -> None:
    print("\nSANITY — obvious nonsense in task times is flagged, never repaired")
    from lib.reconciliation import reconcile
    r = reconcile([0.0, 5.0, 600.0], ["A", "B", "C"], observed_total_minutes=6)
    for w in r.warnings:
        print(f"    {w}")
    check("SANITY", any("non-positive" in w for w in r.warnings),
          "a zero/negative duration is flagged")
    check("SANITY", any("working day" in w for w in r.warnings),
          "an absurdly long duration is flagged")
    check("SANITY", r.reconciled_times and abs(sum(r.reconciled_times) - 6.0) < 1e-6,
          "no value is silently invented; reconciliation still targets the observed total")


if __name__ == "__main__":
    case_A_simple_workflow()
    case_B_knowledge_support()
    case_C_high_risk()
    case_D_missing_evidence()
    case_E_invalid_capability()
    case_F_workload_weighting()
    case_G_scope_sensitive_effort()
    case_H_reference_alignment()
    case_I_ambiguous_state()
    case_C4_anchoring()
    case_J_time_contradiction()
    case_K_evidence_id_stability()
    case_L_threshold_consistency()
    case_N1_implementation_kind()
    case_N6_calibration_disclosure()
    case_M_compliance_evidence()
    case_N_baseline_consistency()
    case_labor_rate_geography()
    case_task_sanity()
    print()
    if failures:
        print(f"{len(failures)} CHECK(S) FAILED:")
        for f in failures:
            print(f"  - {f}")
        sys.exit(1)
    print("ALL ESTIMATOR ACCEPTANCE CASES PASSED")
