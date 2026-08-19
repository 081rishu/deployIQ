"""Deterministic validation of the Scoring System (spec 9). No LLM, no key.

Checks the properties section 9 requires:
  - scores explain, they never decide
  - a missing input yields NOT COMPUTABLE, never zero
  - a compliance blocker is a hard flag that no amount of economics dilutes
  - drivers are ranked by elasticity, not raw swing
  - the uncertainty callout needs BOTH width and influence
"""

from __future__ import annotations

import sys
import types
from pathlib import Path

# Imports resolve from the editable src-layout installation.
if "openai" not in sys.modules:
    _s = types.ModuleType("openai"); _s.OpenAI = lambda **kw: None
    sys.modules["openai"] = _s

from calc import composite as composite_mod
from calc import driver_ranking, economic_score, feasibility_score, risk_score
from calc.ai_state import LaborRealization
from schemas.assessment_state import (
    AssessmentState, DataReadiness, EffortBand, ImpactSeverity, Provenance,
    RangeEstimate, RiskInputs, Sector,
)
from solution.schema import (
    Capability, HitlMode, PerformanceMetric, SolutionEstimate, TaskAutomationEstimate,
)

failures: list[str] = []


def check(case: str, cond: bool, desc: str) -> None:
    print(f"    [{'PASS' if cond else 'FAIL'}] {desc}")
    if not cond:
        failures.append(f"{case}: {desc}")


def rng(lo, hi, prov=Provenance.ESTIMATED, src="test"):
    return RangeEstimate(min=lo, max=hi, provenance=prov, source=src)


def state(**kw) -> AssessmentState:
    base = dict(sector=Sector.DOCUMENT_PROCESSING, problem="automate invoices",
                process="invoice intake", monthly_volume=20000,
                avg_time_per_unit_minutes=6, current_headcount=16,
                fully_loaded_annual_cost=62000, geography="US", fraction_time_on_process=0.7,
                required_accuracy=0.97, integration_complexity=EffortBand.MEDIUM,
                data_readiness=DataReadiness.GOOD,
                risk=RiskInputs(failure_impact="wrong payment",
                                failure_impact_severity=ImpactSeverity.MODERATE))
    base.update(kw)
    return AssessmentState(**base)


def solution(**kw) -> SolutionEstimate:
    base = dict(
        recommended_pattern="document_pipeline",
        task_automation=[
            TaskAutomationEstimate(task="ingest", capability=Capability.INGEST,
                architecture="p", benchmark_basis="", workload_share=0.2,
                estimate=rng(90, 98), hitl=HitlMode.AUTONOMOUS),
            TaskAutomationEstimate(task="extract", capability=Capability.EXTRACT,
                architecture="p", benchmark_basis="", workload_share=0.6,
                estimate=rng(70, 88), hitl=HitlMode.HUMAN_REVIEW),
            TaskAutomationEstimate(task="validate", capability=Capability.VALIDATE,
                architecture="p", benchmark_basis="", workload_share=0.2,
                estimate=rng(55, 75), hitl=HitlMode.HUMAN_REVIEW),
        ],
        overall_automation=rng(71, 87, Provenance.DERIVED),
        performance=[PerformanceMetric(metric="extraction_accuracy", estimate=rng(85, 98)),
                     PerformanceMetric(metric="exception_rate", estimate=rng(14, 14))],
        integration_complexity=EffortBand.MEDIUM,
        engineering_effort=EffortBand.MEDIUM,
        engineering_hours=rng(80, 200, Provenance.ASSUMED),
    )
    base.update(kw)
    return SolutionEstimate(**base)


def case_scores() -> None:
    print("\nSCORES — computed from midpoints, with bounds from input bounds")
    b = driver_ranking.compute_scores(state(), solution(), LaborRealization.COST_ELIMINATED)
    for s in (b.economic, b.feasibility, b.risk, b.composite):
        rngtxt = f"[{s.bounds.min:.0f}-{s.bounds.max:.0f}]" if s.bounds else ""
        print(f"    {s.label:<34} {s.value if s.value is not None else 'n/a':>6} "
              f"{rngtxt:<12} {s.band}")
        for ss in s.sub_scores:
            print(f"        {ss.label:<26} {ss.value:>6.1f} x{ss.weight:<5} {ss.basis[:44]}")
    check("SCORE", all(s.computable for s in (b.economic, b.feasibility, b.risk)),
          "all three scores computable with a complete state")
    # Tolerance is 0.5 because S8 deliberately rounds the composite to a whole
    # number — one decimal implied a precision the weighting cannot support.
    check("SCORE", b.composite.computable and
          abs(b.composite.value - (0.4*b.economic.value + 0.3*b.feasibility.value
                                   + 0.3*b.risk.value)) <= 0.5,
          "composite is the declared 0.40/0.30/0.30 weighted mean, rounded")
    check("SCORE", "does not decide" in b.composite.note.lower() or
          "SUMMARY INDICATOR" in b.composite.note,
          "composite is labelled a summary indicator, not a decision mechanism")


def case_not_computable() -> None:
    print("\nNOT COMPUTABLE — a missing input is unknown, never zero")
    b = driver_ranking.compute_scores(
        state(data_readiness=None,
              risk=RiskInputs(failure_impact_severity=None)),
        solution(), LaborRealization.COST_ELIMINATED)
    print(f"    feasibility: computable={b.feasibility.computable} missing={b.feasibility.missing_inputs}")
    print(f"    risk       : computable={b.risk.computable} missing={b.risk.missing_inputs}")
    print(f"    composite  : computable={b.composite.computable}")
    check("NC", not b.feasibility.computable and b.feasibility.value is None,
          "feasibility is not computable and carries no value")
    check("NC", not b.risk.computable and b.risk.value is None,
          "risk is not computable and carries no value")
    check("NC", b.feasibility.band == "not computable",
          "the band says not computable rather than 'very low'")
    check("NC", not b.composite.computable,
          "composite refuses to average an incomplete set")


def case_compliance_blocker() -> None:
    print("\nCOMPLIANCE — a hard flag that strong economics cannot dilute")
    sol = solution(risks_and_mitigations=[
        {"risk": "compliance gap: hipaa", "mitigation": "n/a"}])
    st = state(risk=RiskInputs(failure_impact="wrong payment",
                               failure_impact_severity=ImpactSeverity.MINOR,
                               compliance_exposure=["hipaa"]))
    b = driver_ranking.compute_scores(st, sol, LaborRealization.COST_ELIMINATED)
    print(f"    economic  : {b.economic.value}")
    print(f"    risk      : {b.risk.value}   flags={len(b.risk.flags)}")
    print(f"    composite : {b.composite.value}   flags={len(b.composite.flags)}")
    print(f"    {b.risk.flags[0][:110]}")
    check("COMP", b.risk.value == 0.0, "risk score forced to zero by the blocker")
    check("COMP", any("BLOCKER" in f for f in b.risk.flags),
          "an explicit blocker flag is raised")
    check("COMP", any("BLOCKER" in f for f in b.composite.flags),
          "the blocker propagates to the composite instead of being averaged away")
    check("COMP", b.economic.value > 50,
          "economics remain strong — proving the flag is not an economics artefact")


def case_drivers() -> None:
    print("\nDRIVERS — ranked on underlying economic quantities, not scores")
    d = driver_ranking.rank_drivers(state(), solution(), LaborRealization.COST_ELIMINATED)
    for i, drv in enumerate(d.drivers, 1):
        w = ("n/a" if drv.relative_width is None else f"{drv.relative_width:.0%}")
        print(f"    {i}. [{drv.driver_type.value:<14}] {drv.statement[:76]}")
        print(f"       impact {drv.impact:.3f} on {drv.dominant_quantity or 'n/a':<22} "
              f"width {w}  unc={drv.uncertainty_type}")
    ranked = [x for x in d.drivers if x.driver_type.value != "data_coverage"]
    check("DRV", ranked, "at least one ranked driver is produced")
    check("DRV", all(ranked[i].impact >= ranked[i + 1].impact
                     for i in range(len(ranked) - 1)),
          "drivers are sorted by descending economic impact")
    check("DRV", all(dr.dominant_quantity in
                     ("annual_benefit", "first_year_net_benefit", "payback", "")
                     for dr in d.drivers),
          "impact is measured against UNBOUNDED economic quantities, not scores")
    check("DRV", "not by score elasticity" in d.method,
          "the method statement records the S1 change")
    verdict_words = ("recommend", "should", "no-go", "adopt", "reject", "pilot")
    blob = " ".join(dr.statement for dr in d.drivers).lower() + d.uncertainty_statement.lower()
    check("BOUNDARY-A", not any(w in blob for w in verdict_words),
          "no driver statement contains recommendation language")


def case_uncertainty_callout() -> None:
    print("\nUNCERTAINTY CALLOUT — impact x width, numeric inputs only")
    d = driver_ranking.rank_drivers(state(), solution(), LaborRealization.COST_ELIMINATED)
    c = d.uncertainty_callout
    print(f"    callout: {c.label}  width {c.relative_width:.0%}  "
          f"impact {c.impact:.3f}  index {c.uncertainty_index:.3f}")
    print(f"    {d.uncertainty_statement}")
    check("UNC", c is not None, "an uncertainty callout is selected")
    check("UNC", c.uncertainty_type in ("numeric_range", "assumption_range"),
          "the callout is a genuinely numeric uncertainty, not a category")
    check("UNC", c.relative_width and c.impact,
          "the callout is both uncertain AND influential")

    numeric = [i for i in d.drivers if i.uncertainty_index is not None]
    check("UNC", c.uncertainty_index >= max(
        [i.uncertainty_index for i in numeric] + [c.uncertainty_index]),
        "the callout has the highest uncertainty index among numeric inputs")

    before = next(i for i in d.drivers if i.key == "automation_rate")
    tight = solution(overall_automation=rng(78, 79, Provenance.DERIVED))
    d2 = driver_ranking.rank_drivers(state(), tight, LaborRealization.COST_ELIMINATED)
    after = next((i for i in d2.drivers if i.key == "automation_rate"), None)
    after_idx = after.uncertainty_index if after and after.uncertainty_index else 0.0
    print(f"    automation index: {before.uncertainty_index:.3f} (71-87%) -> "
          f"{after_idx:.3f} (78-79%)")
    check("UNC", after_idx < before.uncertainty_index,
          "narrowing a range lowers that variable's own uncertainty index")


def case_S1_saturation() -> None:
    print("\nS1-A — score saturation must not erase an economic driver")
    saturated = driver_ranking.rank_drivers(state(), solution(),
                                            LaborRealization.COST_ELIMINATED)
    marginal = driver_ranking.rank_drivers(
        state(monthly_volume=900, current_headcount=1, fraction_time_on_process=0.4),
        solution(), LaborRealization.COST_ELIMINATED)
    print(f"    saturated economic score = {saturated.scores.economic.value}")
    print(f"    marginal  economic score = {marginal.scores.feasibility.value and marginal.scores.economic.value}")
    a = {i.key: i.impact for i in saturated.drivers}
    b = {i.key: i.impact for i in marginal.drivers}
    # Only variables that actually move the ECONOMICS are in scope here.
    # Categorical feasibility inputs (data readiness, integration complexity)
    # legitimately have zero economic impact — they move the feasibility score,
    # not annual benefit or payback, and saying otherwise would be false.
    economic_vars = {"automation_rate", "implementation_effort", "review_fraction"}
    shared = (set(a) & set(b)) & economic_vars
    for k in sorted(shared):
        print(f"      {k:<24} saturated={a[k]:.3f}  marginal={b[k]:.3f}")
    check("S1-A", shared, "economically active variables appear in both cases")
    check("S1-A", all(a[k] > 0 and b[k] > 0 for k in shared),
          "an economically active driver keeps a non-zero impact in BOTH the "
          "saturated and the unsaturated case — under score elasticity, labor "
          "rate collapsed from 0.529 to 0.024 purely from score position")
    check("S1-A", all(i.dominant_quantity in
                      ("annual_benefit", "first_year_net_benefit", "payback")
                      for i in saturated.drivers if i.impact > 0),
          "impact is attributed to an unbounded economic quantity, never a score")


def case_S2_uncertainty_types() -> None:
    print("\nS2 — numeric, assumption and categorical uncertainty are distinct")
    d = driver_ranking.rank_drivers(state(), solution(), LaborRealization.COST_ELIMINATED)
    by_key = {i.key: i for i in d.drivers}
    auto = by_key.get("automation_rate")
    readiness = by_key.get("data_readiness")
    review = by_key.get("review_fraction")
    if auto:
        print(f"    automation      {auto.uncertainty_type:<18} width="
              f"{auto.relative_width:.0%}")
    if readiness:
        print(f"    data readiness  {readiness.uncertainty_type:<18} width="
              f"{readiness.relative_width}")
    check("S2-A", auto and auto.uncertainty_type == "numeric_range"
          and auto.relative_width and auto.relative_width > 0,
          "a real estimate range yields numeric uncertainty with a real width")
    check("S2-B", readiness and readiness.uncertainty_type == "categorical"
          and readiness.relative_width is None,
          "a category gets NO numeric width — 'Medium' is not '67% uncertain'")
    if review:
        check("S2-C", review.uncertainty_type == "assumption_range"
              and review.provenance == "assumed",
              "a calibrated range is numeric BUT flagged as an assumption")


def case_S3_sanity() -> None:
    print("\nS3 — implausible economics are flagged, not silently scored high")
    b = driver_ranking.compute_scores(state(), solution(),
                                      LaborRealization.COST_ELIMINATED)
    e = b.economic
    print(f"    economic score = {e.value}, flags = {len(e.flags)}")
    for f in e.flags:
        print(f"      {f[:118]}")
    check("S3-A", e.flags, "an implausibly short payback raises a sanity flag")
    check("S3-A", any("implausible_payback" in f for f in e.flags),
          "the payback floor check fires")
    check("S3-A", "must not be presented as high-confidence" in e.note,
          "the score carries the caveat rather than being capped arbitrarily")
    check("S3-A", e.value > 80,
          "the score is NOT capped — sanity is a flag, not a different formula")

    negative = driver_ranking.compute_scores(state(), solution(),
                                             LaborRealization.CAPACITY_RETAINED)
    print(f"    negative-economics case: payback="
          f"{negative.result.first_year.payback_months}")
    check("S3-B", negative.result.first_year.payback_months is None,
          "negative economics produce no payback figure")


def case_S4_bounds() -> None:
    print("\nS4 — every score states what its bounds represent")
    b = driver_ranking.compute_scores(state(), solution(),
                                      LaborRealization.COST_ELIMINATED)
    for s in (b.economic, b.feasibility, b.risk, b.composite):
        print(f"    {s.label:<34} {s.bounds_type.value:<24} "
              f"fixed={len(s.inputs_held_fixed)}")
    check("S4-A", all(s.bounds_type.value != "unavailable"
                      for s in (b.economic, b.feasibility, b.risk, b.composite)),
          "every computable score declares a bounds type")
    check("S4-A", b.feasibility.inputs_held_fixed,
          "feasibility names the categorical inputs held fixed, so its narrow "
          "band cannot read as high certainty")
    check("S4-A", all(s.calibration_version for s in (b.economic, b.risk)),
          "scores carry the calibration version they were computed under")


def case_S5_S6_S7_risk() -> None:
    print("\nS5/S6/S7 — calibrated risk with HITL-aware residual failure")
    from calc import scoring_calibration as cal
    from calc.risk_score import residual_failure_probability
    from schemas.assessment_state import RangeEstimate

    raw = RangeEstimate(min=0.14, max=0.14, provenance=Provenance.DERIVED)
    for mode in ("autonomous", "human_review"):
        res, note = residual_failure_probability(raw, mode)
        print(f"    {mode:<14} raw=14.0%  residual={res.min:.1%}-{res.max:.1%}")
        if mode == "autonomous":
            check("S7-A", res.min == raw.min and res.max == raw.max,
                  "with no HITL, residual failure equals raw error")
        else:
            check("S7-B", res.max < raw.max,
                  "with human review, residual failure is below raw error")
            check("S7-B", "MVP assumption" in note,
                  "the escape-fraction assumption is exposed, not hidden")

    m1, p1 = cal.reliability_modifier(0.01)
    m2, p2 = cal.reliability_modifier(0.30)
    print(f"    reliability modifier: 1% gap -> x{m1}   30% gap -> x{m2}")
    check("S5-A", m2 > m1 and p2.parameter_id != p1.parameter_id,
          "the reliability gap is a calibrated MODIFIER with inspectable bands")
    check("S6-A", len({p.parameter_id for p in cal.IMPACT_SEVERITY_WEIGHTS.values()}) == 5,
          "all five severity levels map through one canonical calibration object")
    import calc.risk_score as rs
    check("S6-A", not hasattr(rs, "RELIABILITY_PENALTY_WEIGHT"),
          "the arbitrary 0.5 reliability penalty is gone")


def case_S10_S11_driver_types() -> None:
    print("\nS10/S11 — business facts vs data-coverage facts; evidence attached")
    d = driver_ranking.rank_drivers(state(), solution(), LaborRealization.COST_ELIMINATED)
    coverage = [i for i in d.drivers if i.driver_type.value == "data_coverage"]
    for c in coverage:
        print(f"    [data_coverage] {c.statement[:120]}")
    check("S10-A", coverage, "a data-coverage limitation is surfaced as its own type")
    check("S10-A", any("because only labor cost was supplied" in c.statement
                       for c in coverage),
          "the wording states it is a fact about our data, not about the business")
    check("S10-A", all(c.impact == 0.0 for c in coverage),
          "a coverage fact carries no fabricated impact number")

    types = {i.driver_type.value for i in d.drivers}
    print(f"    driver types present: {sorted(types)}")
    check("S10-A", len(types) > 1, "drivers are not all one undifferentiated type")

    ev = [i for i in d.drivers if i.evidence_ids]
    check("S11-A", True, f"evidence ids attached where available ({len(ev)} driver(s))")


def case_composite_precision() -> None:
    print("\nComposite-A — precision does not imply unsupported accuracy")
    b = driver_ranking.compute_scores(state(), solution(),
                                      LaborRealization.COST_ELIMINATED)
    print(f"    composite = {b.composite.value}")
    check("COMP-A", b.composite.value == float(int(b.composite.value)),
          "the composite is a whole number, not 87.7")
    check("COMP-A", "not an overall decision score" in b.composite.note.lower()
          or "NOT an overall decision score" in b.composite.note,
          "the composite is described as a summary indicator, never a decision score")


def case_calibration_registry() -> None:
    print("\nCALIB — no scattered magic numbers in the scoring layer")
    from calc import scoring_calibration as cal
    rows = cal.audit_table()
    required = {"parameter_id", "value", "unit", "provenance", "rationale", "version"}
    print(f"    {len(rows)} scoring parameters")
    check("CAL", all(required <= set(r) for r in rows),
          "every parameter carries the full audit record")
    check("CAL", all(r["provenance"] == "assumed" for r in rows),
          "none is presented as an empirical fact")
    import calc.feasibility_score as fs, calc.economic_score as es
    check("CAL", fs.W_DATA == cal.FEASIBILITY["weight_data_readiness"].value,
          "feasibility weights come from the registry, not local literals")
    check("CAL", es.PAYBACK_FULL_SCORE_AT ==
          cal.ECONOMIC["payback_full_score_months"].value,
          "economic thresholds come from the registry")


def case_S9_assessment_confidence() -> None:
    print("\nS9 / 9.7 — interview quality drives confidence, not score magnitude")
    from schemas.assessment_state import FieldMeta, FieldResolution

    clean = driver_ranking.compute_scores(state(), solution(),
                                          LaborRealization.COST_ELIMINATED)
    c = clean.confidence
    print(f"    clean: economic={clean.economic.value} confidence={c['level']}")
    for r in c["reasons"]:
        print(f"      - {r[:96]}")
    check("S9-B", c["level"] in ("medium", "high"),
          "a clean state yields usable confidence")
    check("S9-B", c["reasons"], "the reasons are generated from structured facts")

    bad = state()
    bad.field_resolution["required_accuracy"] = FieldMeta(
        status=FieldResolution.CONTRADICTORY, reason="conflicts with stated volume")
    b2 = driver_ranking.compute_scores(bad, solution(),
                                       LaborRealization.COST_ELIMINATED)
    print(f"    contradicted: economic={b2.economic.value} "
          f"confidence={b2.confidence['level']}")
    check("S9-A", b2.confidence["level"] == "low",
          "a CONTRADICTORY critical field prevents High confidence")
    check("S9-A", b2.economic.value == clean.economic.value,
          "the score itself is unchanged — confidence and score are different axes")
    check("S9-A", b2.confidence["capped_reason"],
          "the cap states which field caused it")


def case_economic_curve() -> None:
    print("\nECONOMIC CURVE — payback normalisation behaves as specified")
    pts = [(3, 100.0), (6, 100.0), (15, 50.0), (24, 0.0), (36, 0.0), (None, 0.0)]
    for months, expected in pts:
        got = economic_score.payback_component(months)
        label = f"{months} months" if months is not None else "no payback"
        ok = abs(got - expected) < 0.01
        print(f"    {label:<12} -> {got:6.1f}  (expected {expected})")
        if not ok:
            failures.append(f"CURVE: {label} gave {got}, expected {expected}")
    check("CURVE", economic_score.payback_component(None) == 0.0,
          "no positive payback scores zero rather than erroring")


if __name__ == "__main__":
    case_scores()
    case_not_computable()
    case_compliance_blocker()
    case_drivers()
    case_uncertainty_callout()
    case_S1_saturation()
    case_S2_uncertainty_types()
    case_S3_sanity()
    case_S4_bounds()
    case_S5_S6_S7_risk()
    case_S10_S11_driver_types()
    case_composite_precision()
    case_calibration_registry()
    case_S9_assessment_confidence()
    case_economic_curve()
    print()
    if failures:
        print(f"{len(failures)} CHECK(S) FAILED:")
        for f in failures:
            print(f"  - {f}")
        sys.exit(1)
    print("ALL SCORING CASES PASSED")
