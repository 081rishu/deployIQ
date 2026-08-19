"""Deterministic validation of the Economic Engine (spec 8). No LLM, no key.

Checks the properties section 8 actually requires, not just that it runs:
  - the two labor formulations are compared, never merged
  - automation does not silently become headcount reduction
  - absent components are absent, not zero
  - payback is suppressed when it is not real
  - benchmarks compare, never add
"""

from __future__ import annotations

import sys
import types
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
if "openai" not in sys.modules:
    _s = types.ModuleType("openai"); _s.OpenAI = lambda **kw: None
    sys.modules["openai"] = _s

from calc import sensitivity
from calc.engine import EconomicInputError
from calc.ai_state import LaborRealization
from calc.engine import run
from calc.models import midpoint
from schemas.assessment_state import (
    AssessmentState, EffortBand, Provenance, RangeEstimate, Sector,
)
from solution.schema import (
    HitlMode, PerformanceMetric, SolutionEstimate, TaskAutomationEstimate,
)
from solution.schema import Capability

failures: list[str] = []


def check(case: str, cond: bool, desc: str) -> None:
    print(f"    [{'PASS' if cond else 'FAIL'}] {desc}")
    if not cond:
        failures.append(f"{case}: {desc}")


def rng(lo, hi, prov=Provenance.ESTIMATED, src="test"):
    return RangeEstimate(min=lo, max=hi, provenance=prov, source=src)


def doc_state(**kw) -> AssessmentState:
    base = dict(sector=Sector.DOCUMENT_PROCESSING, problem="automate invoices",
                process="invoice intake and coding", monthly_volume=20000,
                avg_time_per_unit_minutes=6, current_headcount=8,
                fully_loaded_annual_cost=62000, geography="US", fraction_time_on_process=0.7,
                required_accuracy=0.97, integration_complexity=EffortBand.MEDIUM)
    base.update(kw)
    return AssessmentState(**base)


def solution(hitl=HitlMode.HUMAN_REVIEW, automation=(70, 88)) -> SolutionEstimate:
    return SolutionEstimate(
        recommended_pattern="document_pipeline",
        task_automation=[
            TaskAutomationEstimate(task="ingest", capability=Capability.INGEST,
                architecture="pipeline", benchmark_basis="", workload_share=0.2,
                estimate=rng(90, 98), hitl=HitlMode.AUTONOMOUS),
            TaskAutomationEstimate(task="extract line items", capability=Capability.EXTRACT,
                architecture="pipeline", benchmark_basis="", workload_share=0.6,
                estimate=rng(*automation), hitl=hitl),
            TaskAutomationEstimate(task="validate against PO", capability=Capability.VALIDATE,
                architecture="pipeline", benchmark_basis="", workload_share=0.2,
                estimate=rng(55, 75), hitl=HitlMode.HUMAN_REVIEW),
        ],
        overall_automation=rng(71, 87, Provenance.DERIVED),
        performance=[PerformanceMetric(metric="extraction_accuracy", estimate=rng(85, 98))],
        integration_complexity=EffortBand.MEDIUM,
        engineering_effort=EffortBand.MEDIUM,
        engineering_hours=rng(80, 200, Provenance.ASSUMED),
    )


def case_baseline() -> None:
    print("\nBASELINE — 20k invoices/mo, 6 min each, $62k loaded, 70% on process")
    r = run(doc_state(), solution(), LaborRealization.COST_ELIMINATED)
    c = r.labor_consistency
    print(f"    task-based      : {c.task_based.min:>12,.0f} - {c.task_based.max:,.0f}")
    print(f"    workforce-based : {c.workforce_based.min:>12,.0f} - {c.workforce_based.max:,.0f}")
    print(f"    divergence      : {c.divergence:.1%}")
    print(f"    current annual  : {r.current_annual_total.min:>12,.0f} - {r.current_annual_total.max:,.0f}")
    print(f"    AI operating    : {r.ai_operating_total.min:>12,.0f} - {r.ai_operating_total.max:,.0f}")
    print(f"    implementation  : {r.implementation_total.min:>12,.0f} - {r.implementation_total.max:,.0f}")
    print(f"    payback         : {r.first_year.payback_statement}")

    # 20000*12 = 240,000 units/yr * 0.1h = 24,000 h * (62000/2080 = 29.81/h)
    expected = 240_000 * 0.1 * (62_000 / 2080)
    check("BASE", abs(midpoint(c.task_based) - expected) < 1.0,
          f"task-based labor matches hand calculation ({expected:,.0f})")
    check("BASE", c.comparable and c.task_based != c.workforce_based,
          "both formulations computed and kept separate")
    check("BASE", "NOT averaged" in c.verdict or c.divergence <= 0.25,
          "formulations are compared, never merged")
    check("BASE", any(l.status.value == "absent" for l in r.current_annual_cost.lines),
          "uncollected current-cost components are marked absent, not zero")
    check("BASE", r.current_annual_total.min == r.current_annual_cost.known_lines[0].amount.min,
          "absent components contribute nothing to the total (baseline is a floor)")


def case_capacity_vs_cost() -> None:
    print("\nREALIZATION — automation must not silently become headcount reduction")
    eliminated = run(doc_state(), solution(), LaborRealization.COST_ELIMINATED)
    retained = run(doc_state(), solution(), LaborRealization.CAPACITY_RETAINED)
    e_sav = midpoint(eliminated.first_year.annual_cost_savings)
    r_sav = midpoint(retained.first_year.annual_cost_savings)
    print(f"    cost_eliminated   savings: {e_sav:>12,.0f}   {eliminated.first_year.payback_statement[:48]}")
    print(f"    capacity_retained savings: {r_sav:>12,.0f}   {retained.first_year.payback_statement[:48]}")
    print(f"    freed capacity value     : {midpoint(retained.freed_capacity_value):>12,.0f}")
    check("REAL", e_sav > r_sav,
          "retaining headcount yields materially lower savings than eliminating cost")
    check("REAL", r_sav <= 0,
          "with headcount retained, the AI scenario shows no labor cost savings")
    check("REAL", retained.first_year.payback_months is None,
          "no payback is claimed when there is no positive net benefit")
    check("REAL", midpoint(retained.freed_capacity_value) > 0,
          "freed capacity is still quantified, just not banked as savings")
    check("REAL", "capacity, not savings" in retained.realization_statement,
          "the result states which policy produced it")


def case_assisted_not_elimination() -> None:
    print("\nHITL — an AI-assisted task is a productivity gain, not work removal")
    assisted = run(doc_state(), solution(hitl=HitlMode.AI_ASSISTED),
                   LaborRealization.COST_ELIMINATED)
    autonomous = run(doc_state(), solution(hitl=HitlMode.AUTONOMOUS),
                     LaborRealization.COST_ELIMINATED)
    a_task = next(t for t in assisted.tasks if t.task == "extract line items")
    n_task = next(t for t in autonomous.tasks if t.task == "extract line items")
    print(f"    ai_assisted residual work : {a_task.residual_work_fraction.min:.2f}-{a_task.residual_work_fraction.max:.2f}")
    print(f"    autonomous  residual work : {n_task.residual_work_fraction.min:.2f}-{n_task.residual_work_fraction.max:.2f}")
    print(f"    mechanism: {a_task.mechanism}")
    check("HITL", a_task.residual_work_fraction.min > n_task.residual_work_fraction.min,
          "assisted work leaves more residual labor than autonomous work")
    check("HITL", a_task.residual_work_fraction.min > 0.0,
          "assisted labor can never reach zero (a worker remains in the loop)")
    check("HITL", n_task.human_review_cost is None and
          next(t for t in run(doc_state(), solution(), LaborRealization.COST_ELIMINATED).tasks
               if t.task == "extract line items").human_review_cost is not None,
          "human review is costed only where the HITL mode requires it")


def case_no_payback() -> None:
    print("\nNO PAYBACK — tiny volume cannot justify the build")
    r = run(doc_state(monthly_volume=120, current_headcount=1,
                      fraction_time_on_process=0.05),
            solution(), LaborRealization.COST_ELIMINATED)
    print(f"    current annual : {midpoint(r.current_annual_total):>12,.0f}")
    print(f"    savings        : {midpoint(r.first_year.annual_cost_savings):>12,.0f}")
    print(f"    statement      : {r.first_year.payback_statement}")
    check("NOPAY", r.first_year.payback_months is None,
          "no payback figure is produced")
    check("NOPAY", "No positive payback" in r.first_year.payback_statement or
          "No payback can be stated" in r.first_year.payback_statement,
          "the absence of payback is stated plainly, not as a huge number")


def case_benchmark_crosscheck() -> None:
    print("\nBENCHMARK — cross-check compares, never adds")
    r = run(doc_state(), solution(), LaborRealization.COST_ELIMINATED)
    b = r.benchmark
    print(f"    {b.statement[:150]}")
    total_before = midpoint(r.current_annual_total)
    check("BENCH", b.available and b.verdict in
          ("within benchmark range", "above benchmark", "below benchmark"),
          f"a verdict is produced ({b.verdict})")
    check("BENCH", "never added" in b.statement,
          "the output states the benchmark is not additive")
    check("BENCH", midpoint(r.current_annual_total) == total_before,
          "the baseline total is unchanged by the cross-check")
    check("BENCH", b.benchmark_provenance == "sourced",
          "the document-processing benchmark used is a sourced figure")


def case_sensitivity() -> None:
    print("\nSENSITIVITY — recalculation only, no ranking")
    rep = sensitivity.sweep(doc_state(), solution(), LaborRealization.COST_ELIMINATED)
    print(f"    metric: {rep.metric}  baseline: {rep.baseline:,.0f}")
    for i in sorted(rep.impacts, key=lambda x: x.swing, reverse=True):
        print(f"      {i.label:<22} {i.low_metric:>12,.0f} -> {i.high_metric:>12,.0f}"
              f"   swing {i.swing:>11,.0f}  ({i.direction})")
    check("SENS", all(i.failed is None for i in rep.impacts),
          "every variable recalculated without error")
    check("SENS", any(i.swing > 0 for i in rep.impacts),
          "at least one variable moves the outcome")
    check("SENS", "Decision Driver" in rep.note,
          "the report defers ranking to the Decision Driver module")


def case_E5_labor_divergence() -> None:
    print("\nE5 — diverging labor formulations are classified, never auto-selected")
    r = run(doc_state(), solution(), LaborRealization.COST_ELIMINATED)
    c = r.labor_consistency
    print(f"    task={c.task_based.min:>10,.0f}  workforce={c.workforce_based.min:>10,.0f}  "
          f"divergence={c.divergence:.0%}  status={c.status.value}")
    print(f"    PRIMARY = {r.current_annual_total.min:,.0f} ({r.baseline_basis})")
    check("E5-B", c.status.value == "divergent", "a 51% gap is classified as divergent")
    check("E5-B", r.current_annual_total.min == c.workforce_based.min,
          "the workforce formulation is primary — the LARGER task figure is not "
          "selected automatically")
    check("E5-B", c.secondary is not None,
          "the task-based scenario stays inspectable as secondary")
    check("E5-B", "NOT averaged" in c.verdict, "the verdict states they are not merged")

    # E5-A: consistent formulations.
    # 16 FTE x 62,000 x 0.7 = 694,400, within tolerance of the 715,385 the
    # volume/handling-time formulation gives.
    consistent = run(doc_state(current_headcount=16), solution(),
                     LaborRealization.COST_ELIMINATED)
    print(f"    consistent case: status={consistent.labor_consistency.status.value}")
    check("E5-A", consistent.labor_consistency.status.value == "consistent",
          "formulations within tolerance are classified consistent")

    # E5-D: neither defensible.
    try:
        run(doc_state(monthly_volume=None, current_headcount=None,
                      avg_time_per_unit_minutes=None), solution(),
            LaborRealization.COST_ELIMINATED)
        check("E5-D", False, "no baseline should be manufactured")
    except EconomicInputError as exc:
        print(f"    no inputs: {exc.reasons[0][:80]}")
        check("E5-D", True, "with neither formulation, the engine refuses rather "
                            "than manufacturing a baseline")


def case_E6_quality_symmetry() -> None:
    print("\nE6 — current quality is ABSENT, not 100%; metrics are not interchangeable")
    r = run(doc_state(), solution(), LaborRealization.COST_ELIMINATED)
    q = r.quality_comparison
    print(f"    comparable={q['comparable']}")
    print(f"    {q['statement'][:120]}")
    check("E6-A", not q["comparable"], "no comparison is made against unmeasured "
                                       "current quality")
    check("E6-A", "NOT assumed to be 100%" in q["statement"],
          "the result states current quality is not assumed perfect")

    from calc.quality import from_estimator_metric, QualityMetric
    exc = from_estimator_metric("exception_rate",
                                RangeEstimate(min=14, max=14, provenance=Provenance.SOURCED))
    check("E6-B", exc.metric == QualityMetric.NON_EXCEPTION_RATE,
          "a 14% exception rate becomes an 86% NON-EXCEPTION rate, not 'accuracy'")


def case_E7_real_ranges() -> None:
    print("\nE7 — sensitivity bounds come from each input's own range")
    rep = sensitivity.sweep(doc_state(), solution(), LaborRealization.COST_ELIMINATED)
    for i in sorted(rep.impacts, key=lambda x: -x.swing):
        print(f"    {i.label:<22} swing {i.swing:>10,.0f}   bounds: {i.bounds}")
    auto = next((i for i in rep.impacts if i.variable == "automation_scale"), None)
    check("E7-A", auto is not None and "71-87%" in auto.bounds,
          "automation is swept at its estimator range, not an invented +/-30%")
    check("E7-B", all(i.provenance and i.source for i in rep.impacts),
          "every swept variable declares provenance and a source")
    review = next((i for i in rep.impacts if i.variable == "review_fraction"), None)
    check("E7-B", review is not None and "calibration" in review.source,
          "an assumption-based variable carries its calibration rationale")


def case_E8_geography() -> None:
    print("\nE8 — geography controls the labor rate; no silent US fallback")
    us = run(doc_state(geography="US", fully_loaded_annual_cost=None),
             solution(), LaborRealization.COST_ELIMINATED)
    print(f"    US        -> resolved, geography={us.labor_rate_geography}")
    check("E8-B", us.labor_rate_geography == "US", "a US assessment resolves to US rates")

    # India now RESOLVES through the labor-rate registry (docs/labor_rates.json),
    # in INR. Before that evidence existed it correctly refused; the test that
    # asserted refusal encoded the absence of data, not the desired behaviour.
    india = run(doc_state(geography="India", fully_loaded_annual_cost=None),
                solution(), LaborRealization.COST_ELIMINATED)
    print(f"    India     -> resolved in INR, geography={india.labor_rate_geography}")
    check("E8-A", india.labor_rate_geography == "India",
          "an India assessment uses India rates, never US ones")

    for geo, label in ((None, "unknown"), ("Germany", "unlisted")):
        try:
            run(doc_state(geography=geo, fully_loaded_annual_cost=None), solution(),
                LaborRealization.COST_ELIMINATED)
            check("E8-C", False, f"{label} geography must not silently resolve")
        except EconomicInputError as exc:
            print(f"    {label:<9} -> {exc.reasons[0][:70]}")
            check("E8-C", True,
                  f"{label} geography is unresolved rather than defaulted")


def case_E9_task_shares() -> None:
    print("\nE9 — an unresolved share default cannot become a fake equal split")
    sol = solution()
    for t in sol.task_automation:
        t.workload_share = 1.0
        t.workload_share_provenance = Provenance.ASSUMED
    try:
        run(doc_state(), sol, LaborRealization.COST_ELIMINATED)
        check("E9-A", False, "unresolved shares must be rejected")
    except EconomicInputError as exc:
        print(f"    {exc.reasons[0][:110]}")
        check("E9-A", True, "1/1/1 unresolved shares are rejected, not normalised "
                            "to 0.33/0.33/0.33")
    ok = run(doc_state(), solution(), LaborRealization.COST_ELIMINATED)
    check("E9-B", ok.tasks and abs(sum(t.workload_share for t in ok.tasks) - 1.0) < 1e-6,
          "genuinely derived shares are accepted and sum to 1")


def case_E1_current_costs() -> None:
    print("\nE1 — tooling / rework / other direct costs enter the baseline")
    bare = run(doc_state(), solution(), LaborRealization.COST_ELIMINATED)
    full = run(doc_state(annual_tooling_cost=48000, error_rate=0.08,
                         rework_time_per_error_minutes=25,
                         annual_other_direct_cost=12000),
               solution(), LaborRealization.COST_ELIMINATED)
    print(f"    labor only : {midpoint(bare.current_annual_total):>10,.0f}  "
          f"({len(bare.current_annual_cost.absent_lines)} absent)")
    print(f"    with inputs: {midpoint(full.current_annual_total):>10,.0f}  "
          f"({len(full.current_annual_cost.absent_lines)} absent)")
    check("E1", midpoint(full.current_annual_total) > midpoint(bare.current_annual_total),
          "supplying the components raises the baseline above the labor-only floor")
    check("E1", len(bare.current_annual_cost.absent_lines) == 3,
          "uncollected components stay ABSENT rather than becoming zero")


def case_E2_architecture_pricing() -> None:
    print("\nE2 — AI operating cost follows the selected architecture")
    doc = run(doc_state(), solution(), LaborRealization.COST_ELIMINATED)
    cs_sol = solution(); cs_sol.recommended_pattern = "ai_assisted_workflow"
    cs = run(doc_state(sector=Sector.CUSTOMER_SUPPORT), cs_sol,
             LaborRealization.COST_ELIMINATED)
    doc_line = next(l for l in doc.ai_operating.lines if l.key == "inference")
    cs_line = next(l for l in cs.ai_operating.lines if l.key == "inference")
    print(f"    document processing: {midpoint(doc_line.amount):>10,.0f}  {doc.inference_pricing_ids}")
    print(f"    customer support   : {midpoint(cs_line.amount):>10,.0f}  {cs.inference_pricing_ids}")
    check("E2-A", doc.inference_pricing_ids != cs.inference_pricing_ids,
          "different architectures are priced from different records")
    check("E2-A", cs_line.amount is not None,
          "customer support has a real inference cost (was ABSENT, making AI look free)")
    check("E2-A", any("sourced" in s for s in cs.inference_lineage),
          "the token PRICE is sourced while usage stays an assumption")
    check("E2-B", all(l.status.value == "absent" or l.amount is not None
                      for l in doc.ai_operating.lines),
          "a component with no pricing record is ABSENT, never zero")


def case_E3_review_architecture() -> None:
    print("\nE3 — review is derived from HITL, not a universal 20%")
    costs = {}
    for mode in (HitlMode.HUMAN_REVIEW, HitlMode.ESCALATION, HitlMode.AUTONOMOUS):
        r = run(doc_state(), solution(hitl=mode), LaborRealization.COST_ELIMINATED)
        line = next((l for l in r.ai_operating.lines if l.key == "human_review"), None)
        costs[mode.value] = midpoint(line.amount) if line and line.amount else 0.0
        print(f"    {mode.value:<14} review = {costs[mode.value]:>10,.0f}")
    check("E3-B", len(set(costs.values())) > 1,
          "different HITL architectures do NOT receive the same review cost")
    check("E3-B", costs["escalation"] > costs["human_review"],
          "escalation costs more than review, as the calibration states")
    from calc import calibration as cal
    check("E3-A", all(p.provenance == Provenance.ASSUMED and p.rationale
                      for p in cal.all_params()),
          "every review/maintenance/stage parameter is an assumption with a rationale")


def case_E11_reliability() -> None:
    print("\nE11 — a reliability gap costs money only when the consequence is known")
    bare = run(doc_state(), solution(), LaborRealization.COST_ELIMINATED)
    known = run(doc_state(rework_time_per_error_minutes=20), solution(),
                LaborRealization.COST_ELIMINATED)
    print(f"    no rework time : gap={bare.reliability['gap']:.1%} "
          f"costable={bare.reliability['costable']}")
    print(f"    rework known   : gap={known.reliability['gap']:.1%} "
          f"costable={known.reliability['costable']}")
    check("E11-A", not bare.reliability["costable"],
          "an uncostable gap stays a qualitative risk, not a fabricated figure")
    check("E11-A", known.reliability["costable"],
          "a gap with known rework time becomes an economic line item")
    line = next(l for l in known.ai_operating.lines if l.key == "reliability_gap")
    check("E11-A", line.amount is not None and midpoint(line.amount) > 0,
          "the line carries a real cost")


def india_state(**kw) -> AssessmentState:
    base = dict(sector=Sector.DOCUMENT_PROCESSING, problem="automate invoices",
                process="invoice intake", geography="India", monthly_volume=20000,
                avg_time_per_unit_minutes=6, current_headcount=16,
                fraction_time_on_process=0.7, required_accuracy=0.97)
    base.update(kw)
    return AssessmentState(**base)


def case_labor_registry() -> None:
    print("\nLABOR — geography x role resolution, process vs implementation")
    from lib.labor_rates import LaborKind, fully_loaded, lookup
    from solution.effort_bands import implementation_rate

    cs = lookup("India", LaborKind.PROCESS, "customer_support_agent")
    ap = lookup("India", LaborKind.PROCESS, "accounts_payable_clerk")
    eng = lookup("India", LaborKind.IMPLEMENTATION, "ai_ml_engineer")
    print(f"    support agent -> {cs.entry.rate_id}   AP clerk -> {ap.entry.rate_id}")
    print(f"    engineer      -> {eng.entry.rate_id}")
    check("LABOR", cs.resolved and cs.entry.rate_id == "IN-CS-AGENT-2026",
          "India customer support resolves to the support-agent entry")
    check("LABOR", ap.resolved and ap.entry.rate_id == "IN-AP-CLERK-2026",
          "India document processing resolves to the AP clerk entry")
    check("LABOR", eng.resolved and eng.entry.labor_kind == LaborKind.IMPLEMENTATION,
          "AI implementation resolves to engineering labor")

    swapped = lookup("India", LaborKind.PROCESS, "ai_ml_engineer")
    print(f"    engineer-as-process -> {'UNRESOLVED' if not swapped.resolved else 'RESOLVED'}")
    check("LABOR", not swapped.resolved,
          "process and engineering labor cannot be swapped")
    check("LABOR", not lookup("Germany", LaborKind.PROCESS, "customer_support_agent").resolved,
          "an unlisted geography is UNRESOLVED, not substituted")
    check("LABOR", not lookup(None, LaborKind.PROCESS).resolved,
          "a missing geography is UNRESOLVED")

    entry = cs.entry
    comp = entry.compensation_hourly()
    loaded, note = fully_loaded(entry)
    print(f"    market comp {comp.min:,.0f}-{comp.max:,.0f} -> loaded "
          f"{loaded.min:,.0f}-{loaded.max:,.0f} {entry.currency}")
    check("LABOR", comp.provenance == Provenance.SOURCED,
          "market compensation keeps its sourced provenance")
    check("LABOR", loaded.provenance == Provenance.DERIVED and "unresolved" in note,
          "the fully-loaded figure is DERIVED via a multiplier whose status is "
          "unresolved — not presented as sourced")
    check("LABOR", entry.rate_id in (loaded.source_id or ""),
          "the source rate remains auditable after the load adjustment")


def case_india_end_to_end() -> None:
    print("\nINDIA — an India assessment now costs instead of refusing")
    r = run(india_state(), solution(), LaborRealization.COST_ELIMINATED)
    print(f"    geography={r.labor_rate_geography}  "
          f"current={midpoint(r.current_annual_total):,.0f} INR  "
          f"AI ops={midpoint(r.ai_operating_total):,.0f} INR")
    check("INDIA", r.labor_rate_geography == "India",
          "the India rate registry is used, not a US fallback")
    check("INDIA", midpoint(r.current_annual_total) > 0,
          "a full economic result is produced")

    infer = next(l for l in r.ai_operating.lines if l.key == "inference")
    print(f"    inference: {infer.status.value} — {infer.note[:88]}")
    check("CURRENCY", infer.amount is None,
          "USD provider pricing is NOT added to an INR baseline")
    check("CURRENCY", any("currency" in w.lower() for w in r.warnings),
          "the currency mismatch is stated rather than absorbed")


def case_compliance_registers() -> None:
    print("\nCOMPLIANCE — two registers, and a vendor attestation binds only its own")
    from lib.compliance import ClaimStatus, evaluate_implementation
    from lib.vendor_attestations import backs_implementation, load_registry

    reg = load_registry()
    print(f"    product-vendor attestations : {len(reg.product_vendor_attestations)}")
    print(f"    registry-impl attestations  : {len(reg.registry_implementation_attestations)}")
    check("COMP", reg.product_vendor_attestations,
          "deployIQ's own vendor attestations are recorded")
    check("COMP", all(a.evidence_type == "vendor_published_attestation"
                      and not a.is_independent
                      for a in reg.product_vendor_attestations),
          "vendor attestations are NOT labelled independent verification")

    ok, why = backs_implementation("att_anthropic_2026", "n8n", "hipaa")
    check("COMP", not ok,
          "an attestation for our own LLM vendor cannot qualify n8n for HIPAA")
    ok2, _ = backs_implementation("no_such_evidence", "n8n", "hipaa")
    check("COMP", not ok2, "an unknown evidence_id backs nothing")

    # Hard filtering now reads the evidence registry, never inline claims.
    check("COMP", not evaluate_implementation("make", "HIPAA").satisfies,
          "an UNKNOWN verdict satisfies nothing")
    check("COMP", evaluate_implementation("make", "SOC 2").satisfies,
          "an evidence-backed verdict does satisfy")
    check("COMP", evaluate_implementation("zapier", "HIPAA").status
          == ClaimStatus.NOT_APPLICABLE,
          "an explicit vendor exclusion is preserved as incompatible")


def case_provenance_survives() -> None:
    print("\nPROVENANCE — estimated stays estimated, assumed stays assumed")
    r = run(doc_state(), solution(), LaborRealization.COST_ELIMINATED)
    kinds = {}
    for line in r.ai_operating.known_lines:
        kinds[line.key] = line.amount.provenance.value
    for k, v in kinds.items():
        print(f"    {k:<18} {v}")
    check("PROV", r.first_year.annual_cost_savings.provenance == Provenance.DERIVED,
          "derived economics are tagged derived")
    auto = solution().overall_automation
    check("PROV", auto.provenance == Provenance.DERIVED,
          "estimator output keeps its own provenance, distinct from calibration")
    from calc import calibration as cal
    check("PROV", all(p.provenance == Provenance.ASSUMED for p in cal.all_params()),
          "calibration stays `assumed` and is never promoted")
    check("PROV", r.inference_lineage,
          "derived inference cost records its input lineage")


def case_A_B_provenance_end_to_end() -> None:
    print("\nA/B — provenance survives estimator -> engine; estimated != assumed")
    r = run(doc_state(), solution(), LaborRealization.COST_ELIMINATED)
    lin = r.provenance_lineage
    for k, v in lin.items():
        print(f"    {k:<22} {v}")
    check("A", lin, "the engine records a provenance lineage per derived line")
    check("A", "estimated" in lin.get("task_automation", []),
          "estimator output arrives tagged `estimated` and stays that way")
    check("B", "assumed" in lin.get("maintenance", []),
          "calibration arrives tagged `assumed` and stays that way")
    check("B", lin.get("task_automation") != lin.get("maintenance"),
          "an estimator range and a calibration range are NOT collapsed into the "
          "same provenance — both uncertain, different kinds of evidence")
    check("B", "assumed" in lin.get("human_review", [])
          and "estimated" in lin.get("human_review", []),
          "a line fed by both keeps BOTH kinds visible rather than picking one")

    from calc import calibration as cal
    maint = cal.MAINTENANCE_FRACTION.as_range()
    auto = solution().overall_automation
    check("B", maint.provenance == Provenance.ASSUMED
          and auto.provenance != Provenance.ASSUMED,
          "at source: maintenance is `assumed`, automation is not")


def case_J_K_token_provenance() -> None:
    print("\nJ/K — token PRICE is sourced, token USAGE is assumed, neither is zero")
    from lib.pricing import load_pricing
    book = load_pricing()
    price = book.by_id("openai_gpt5_mini_v1")
    usage = book.token_usage("customer_support_ticket")
    print(f"    price {price.input_price}/{price.output_price} per 1M  "
          f"provenance={price.provenance}  source={price.source_url[:40]}")
    print(f"    usage {usage.input_tokens_min:,.0f}-{usage.input_tokens_max:,.0f} in  "
          f"provenance={usage.provenance}")
    check("J", price.provenance == "sourced", "the token price is sourced")
    check("J", usage.provenance == "assumed", "the token usage is an assumption")
    check("J", usage.rationale, "the usage assumption states its reasoning")

    cs_sol = solution(); cs_sol.recommended_pattern = "ai_assisted_workflow"
    cs = run(doc_state(sector=Sector.CUSTOMER_SUPPORT), cs_sol,
             LaborRealization.COST_ELIMINATED)
    line = next(l for l in cs.ai_operating.lines if l.key == "inference")
    check("J", set(cs.provenance_lineage.get("inference", [])) == {"assumed", "sourced"},
          "the inference line records BOTH the sourced price and the assumed usage")
    check("K", line.amount is not None and midpoint(line.amount) > 0,
          "AI usage is not silently zero")

    # And with no pricing record for the geography/currency it goes ABSENT,
    # never zero.
    india = AssessmentState(sector=Sector.DOCUMENT_PROCESSING, problem="p",
                            process="invoice intake", geography="India",
                            monthly_volume=20000, avg_time_per_unit_minutes=6,
                            current_headcount=16, fraction_time_on_process=0.7,
                            required_accuracy=0.97)
    ind = run(india, solution(), LaborRealization.COST_ELIMINATED)
    ind_line = next(l for l in ind.ai_operating.lines if l.key == "inference")
    check("K", ind_line.amount is None and ind_line.status.value == "absent",
          "an unpriceable inference line is ABSENT, never 0")


def case_P_payback_states() -> None:
    print("\nP — payback distinguishes positive / none / range crossing zero")
    positive = run(doc_state(annual_tooling_cost=400000), solution(),
                   LaborRealization.COST_ELIMINATED)
    none_case = run(doc_state(), solution(), LaborRealization.CAPACITY_RETAINED)
    crossing = run(doc_state(monthly_volume=900, current_headcount=1,
                             fraction_time_on_process=0.08),
                   solution(), LaborRealization.COST_ELIMINATED)
    for label, r in (("positive", positive), ("none", none_case),
                     ("crossing zero", crossing)):
        has = r.first_year.payback_months is not None
        print(f"    {label:<14} payback_months={'set' if has else 'None':<5} "
              f"{r.first_year.payback_statement[:76]}")
    check("P", positive.first_year.payback_months is not None,
          "a genuinely positive case reports a payback figure")
    check("P", none_case.first_year.payback_months is None
          and "No positive payback" in none_case.first_year.payback_statement,
          "no positive benefit reports no payback, not a large number")
    check("P", crossing.first_year.payback_months is None
          and "spans zero" in crossing.first_year.payback_statement,
          "a range crossing zero is reported as indeterminate, not midpointed")


def case_calibration_audit() -> None:
    print("\nCALIB — every parameter is versioned, unit-bearing and auditable")
    from calc import calibration as cal
    rows = cal.audit_table()
    required = {"calibration_id", "version", "min", "max", "unit", "provenance",
                "rationale", "last_reviewed"}
    missing = [r["calibration_id"] for r in rows if not required <= set(r)]
    print(f"    {len(rows)} parameters, all fields present: {not missing}")
    check("CALIB", not missing, "every parameter carries the full audit record")
    check("CALIB", all(r["provenance"] == "assumed" for r in rows),
          "none is presented as an empirical fact")
    check("CALIB", all(r["rationale"] for r in rows),
          "every assumption states its reasoning")

    part = cal.stage_partition()
    print(f"    stage partition sums to {sum(part.values()):.6f}")
    check("STAGE", abs(sum(part.values()) - 1.0) < 1e-9,
          "stage allocation PARTITIONS the effort band — it does not lose or "
          "add effort")
    import calc.implementation as impl_mod
    check("STAGE", not hasattr(impl_mod, "STAGE_WEIGHTS")
          and not hasattr(impl_mod, "MAINTENANCE_SHARE_OF_BUILD"),
          "no duplicate calibration constants remain in calc/implementation.py")


def case_final_audit_traces() -> None:
    print("\nAUDIT — section 14 traced cases")

    print("  CASE 1 — India customer support")
    cs = AssessmentState(sector=Sector.CUSTOMER_SUPPORT, problem="p",
                         process="ticket triage", geography="India",
                         monthly_volume=20000, avg_time_per_unit_minutes=6,
                         current_headcount=16, fraction_time_on_process=0.7,
                         required_accuracy=0.95)
    cs_sol = solution(); cs_sol.recommended_pattern = "ai_assisted_workflow"
    r1 = run(cs, cs_sol, LaborRealization.COST_ELIMINATED)
    infer1 = next(l for l in r1.ai_operating.lines if l.key == "inference")
    print(f"    geography={r1.labor_rate_geography}  "
          f"current={midpoint(r1.current_annual_total):,.0f} INR  "
          f"inference={'ABSENT' if infer1.amount is None else 'PRESENT'}")
    check("AUDIT1", r1.labor_rate_geography == "India", "no US fallback")
    check("AUDIT1", infer1.amount is None,
          "no INR/USD mixing — the USD-priced line stays absent")

    print("  CASE 2 — India document processing")
    doc = AssessmentState(sector=Sector.DOCUMENT_PROCESSING, problem="p",
                          process="invoice intake", geography="India",
                          monthly_volume=20000, avg_time_per_unit_minutes=6,
                          current_headcount=16, fraction_time_on_process=0.7,
                          required_accuracy=0.97)
    r2 = run(doc, solution(), LaborRealization.COST_ELIMINATED)
    from lib.labor_rates import LaborKind, lookup
    proc = lookup("India", LaborKind.PROCESS, "accounts_payable_clerk")
    eng = lookup("India", LaborKind.IMPLEMENTATION, "ai_ml_engineer")
    print(f"    process labor={proc.entry.rate_id}  engineering={eng.entry.rate_id}")
    print(f"    implementation={midpoint(r2.implementation_total):,.0f} INR")
    check("AUDIT2", proc.entry.rate_id != eng.entry.rate_id,
          "AP clerk process labor and AI/ML engineering labor are distinct rates")
    check("AUDIT2", midpoint(r2.implementation_total) > 0,
          "implementation cost is computed from engineering labor")

    print("  CASE 3 — economically ambiguous (wide automation + HITL)")
    wide = solution(automation=(20, 95), hitl=HitlMode.HUMAN_REVIEW)
    r3 = run(doc_state(monthly_volume=1100, current_headcount=1,
                       fraction_time_on_process=0.1),
             wide, LaborRealization.COST_ELIMINATED)
    print(f"    savings={r3.first_year.annual_cost_savings.min:,.0f} to "
          f"{r3.first_year.annual_cost_savings.max:,.0f}")
    print(f"    {r3.first_year.payback_statement[:110]}")
    check("AUDIT3", r3.first_year.payback_months is None,
          "an ambiguous case does not manufacture a positive payback")


if __name__ == "__main__":
    case_baseline()
    case_capacity_vs_cost()
    case_assisted_not_elimination()
    case_no_payback()
    case_benchmark_crosscheck()
    case_sensitivity()
    case_E5_labor_divergence()
    case_E6_quality_symmetry()
    case_E7_real_ranges()
    case_E8_geography()
    case_E9_task_shares()
    case_E1_current_costs()
    case_E2_architecture_pricing()
    case_E3_review_architecture()
    case_E11_reliability()
    case_labor_registry()
    case_india_end_to_end()
    case_compliance_registers()
    case_provenance_survives()
    case_A_B_provenance_end_to_end()
    case_J_K_token_provenance()
    case_P_payback_states()
    case_calibration_audit()
    case_final_audit_traces()
    print()
    if failures:
        print(f"{len(failures)} CHECK(S) FAILED:")
        for f in failures:
            print(f"  - {f}")
        sys.exit(1)
    print("ALL ECONOMIC ENGINE CASES PASSED")
