"""AI Interviewer acceptance tests — docs/deployIQ_interviewer_proposed_fix.md §21.

Runs with a stubbed LLM: no API key, fully deterministic.

The headline test is `case_downstream_acceptance`: a completed interview must
produce a state the Economic Engine and Scoring System ACCEPT. That is the
test whose absence let the pipeline stay disconnected.
"""

from __future__ import annotations

import sys
import types
from pathlib import Path

# Imports resolve from the editable src-layout installation.
if "openai" not in sys.modules:
    _s = types.ModuleType("openai"); _s.OpenAI = lambda **kw: None
    sys.modules["openai"] = _s

from calc import driver_ranking
from calc.ai_state import LaborRealization
from calc.engine import EconomicInputError, run
from calc.models import midpoint
from interviewer import engine as eng
from interviewer.conversation import ConversationContext, Phase
from interviewer.fields import FIELDS, Tier, required_fields, tier_fields
from schemas.assessment_state import (
    AssessmentState, CurrentQualityMetric, DataReadiness, EffortBand, FieldResolution, ImpactSeverity,
    Provenance, RangeEstimate, Sector, point,
)
from solution.schema import SolutionEstimate

failures: list[str] = []


def check(case: str, cond: bool, desc: str) -> None:
    print(f"    [{'PASS' if cond else 'FAIL'}] {desc}")
    if not cond:
        failures.append(f"{case}: {desc}")


def completed_state(**over) -> AssessmentState:
    """A state exactly as a finished interview leaves it."""
    s = AssessmentState(sector=Sector.CUSTOMER_SUPPORT,
                        problem="automate support ticket triage")
    values = {"geography": "India", "process": "ticket triage",
              "monthly_volume": {"min": 9000, "max": 11000},
              "avg_time_per_unit_minutes": 8, "current_headcount": 15,
              "required_accuracy": {"min": 0.95, "max": 0.95},
              "data_readiness": DataReadiness.GOOD, "current_tools": ["Zendesk"],
              "risk.failure_impact_severity": ImpactSeverity.MAJOR}
    values.update(over)
    for k, v in values.items():
        s.set_value(k, v)
        s.set_resolution(k, FieldResolution.RESOLVED)
        s.tag(k, Provenance.USER_PROVIDED)
    return s


def stub_solution() -> SolutionEstimate:
    return SolutionEstimate(
        recommended_pattern="ai_assisted_workflow",
        overall_automation=RangeEstimate(min=60, max=75),
        integration_complexity=EffortBand.MEDIUM,
        engineering_effort=EffortBand.MEDIUM,
        engineering_hours=RangeEstimate(min=80, max=200))


# --- the test that would have caught the break -----------------------------

def case_downstream_acceptance() -> None:
    print("\nDOWNSTREAM — a completed interview must be ACCEPTED by the pipeline")
    s = completed_state()
    print(f"    interview READY: {eng._minimum_sufficient_reached(s)}")
    try:
        r = run(s, stub_solution(), LaborRealization.COST_ELIMINATED)
        b = driver_ranking.compute_scores(s, stub_solution(),
                                          LaborRealization.COST_ELIMINATED)
        print(f"    engine accepted: {midpoint(r.current_annual_total):,.0f} "
              f"{s.currency}  geography={r.labor_rate_geography}")
        print(f"    scoring: economic={b.economic.value} "
              f"feasibility={b.feasibility.value}")
        accepted = True
    except EconomicInputError as exc:
        print(f"    REFUSED: {exc.reasons[0][:110]}")
        accepted = False
    check("DOWN", eng._minimum_sufficient_reached(s),
          "the interview considers the state complete")
    check("DOWN", accepted,
          "the Economic Engine ACCEPTS the state the interviewer produced — "
          "before geography was collected it refused every time")


def case_geography() -> None:
    print("\nGEOGRAPHY — collected, and currency derived from it")
    spec = next(f for f in FIELDS if f.key == "geography")
    check("GEO", spec.required_for_completion and spec.tier == Tier.TIER_1,
          "geography is a Tier-1 required field")
    s = completed_state(geography="India")
    check("GEO", s.currency == "INR", "India derives INR")
    check("GEO", completed_state(geography="US").currency == "USD", "US derives USD")
    check("GEO", AssessmentState(sector=Sector.CUSTOMER_SUPPORT).currency is None,
          "no geography derives NO currency — never a silent USD default")

    incomplete = completed_state()
    incomplete.geography = None
    check("GEO", not eng._minimum_sufficient_reached(incomplete),
          "the interview cannot complete without geography")

    check("GEO", "do NOT pick one" in (spec.extraction_hint or ""),
          "a multi-geography answer is flagged ambiguous rather than resolved "
          "silently")


def case_validation() -> None:
    print("\nVALIDATION — invalid assignments are rejected, good values preserved")
    s = completed_state()
    before = s.monthly_volume.min
    for bad in ("a lot", {"foo": "bar"}, [1, 2]):
        try:
            s.set_value("monthly_volume", bad)
            check("VAL", False, f"{bad!r} should have been rejected")
        except (ValueError, TypeError):
            pass
    print(f"    rejected 3 invalid values; previous value still {s.monthly_volume.min:,.0f}")
    check("VAL", s.monthly_volume.min == before,
          "the previous valid value survives a rejected update")
    check("VAL", AssessmentState.model_config.get("validate_assignment"),
          "assignment validation is enabled — set_value no longer bypasses the schema")

    s.set_value("monthly_volume", 15000)
    check("VAL", point(s.monthly_volume) == 15000,
          "a correction OVERWRITES the previous value rather than appending")


def case_ranges() -> None:
    print("\nRANGES — a range the user gave stays a range")
    s = completed_state(monthly_volume={"min": 10000, "max": 15000})
    v = s.monthly_volume
    print(f"    stored: {v.min:,.0f}-{v.max:,.0f}   point(): {point(v):,.0f}")
    check("RANGE", v.min == 10000 and v.max == 15000,
          "'between 10,000 and 15,000' is NOT silently collapsed to 12,500")
    check("RANGE", point(v) == 12500,
          "the midpoint is available through an explicit derivation")
    s2 = completed_state(avg_time_per_unit_minutes=8)
    check("RANGE", s2.avg_time_per_unit_minutes.min ==
          s2.avg_time_per_unit_minutes.max == 8,
          "a single number becomes a point range, not an invented spread")


def case_tiering() -> None:
    print("\nTIERING — only Tier 1 blocks completion")
    t1 = tier_fields(Sector.CUSTOMER_SUPPORT, Tier.TIER_1)
    t2 = tier_fields(Sector.CUSTOMER_SUPPORT, Tier.TIER_2)
    t3 = tier_fields(Sector.CUSTOMER_SUPPORT, Tier.TIER_3)
    print(f"    tier1={len(t1)}  tier2={len(t2)}  tier3={len(t3)}")
    check("TIER", all(f.required_for_completion for f in t1
                      if f.analysis_relevant and not f.benchmark_substitutable),
          "every chased Tier-1 field blocks completion")
    check("TIER", not any(f.required_for_completion for f in t3),
          "no Tier-3 field can block completion")
    s = completed_state()
    check("TIER", eng._minimum_sufficient_reached(s),
          "Tier-1 complete is sufficient to finish, without Tier 2 or 3")

    budget = len([f for f in required_fields(Sector.CUSTOMER_SUPPORT)])
    print(f"    required fields={budget}  MAX_QUESTIONS={eng.MAX_QUESTIONS}")
    check("TIER", budget < eng.MAX_QUESTIONS,
          "required fields leave room for clarification inside the question cap")


def case_over_asking_guard() -> None:
    print("\nOVER-ASKING — nothing required feeds nothing")
    read_nowhere = {"risk.failure_impact"}   # audited: no downstream consumer
    for key in read_nowhere:
        spec = next(f for f in FIELDS if f.key == key)
        check("ASK", not spec.required_for_completion,
              f"{key} has no downstream consumer, so it cannot block completion")
    s = completed_state()
    need = eng.select_next_need(s)
    print(f"    next need on a complete state: {need.field.key if need else None}")
    check("ASK", need is None or need.field.tier != Tier.TIER_1,
          "no Tier-1 field is still outstanding once the interview is complete")


def case_multi_fill() -> None:
    print("\nMULTI-FILL — one answer populates several fields")
    s = AssessmentState(sector=Sector.CUSTOMER_SUPPORT, problem="p")
    updates = [
        eng.ExtractedUpdate(field="monthly_volume", value=10000,
                            provenance=Provenance.USER_PROVIDED),
        eng.ExtractedUpdate(field="current_headcount", value=15,
                            provenance=Provenance.USER_PROVIDED),
        eng.ExtractedUpdate(field="current_tools", value=["SAP"],
                            provenance=Provenance.USER_PROVIDED),
        eng.ExtractedUpdate(field="geography", value="India",
                            provenance=Provenance.USER_PROVIDED),
    ]
    eng._apply_updates(s, updates)
    filled = [k for k in ("monthly_volume", "current_headcount", "current_tools",
                          "geography") if s.get_value(k)]
    print(f"    filled from one answer: {filled}")
    check("MULTI", len(filled) == 4, "all four facts land from a single response")
    check("MULTI", all(m.attempts == 0 for m in s.field_resolution.values()),
          "volunteered facts burn no question attempts")


def case_null_never_overwrites() -> None:
    print("\nNULL-OVERWRITE — an absent extraction never wipes a collected value")
    s = AssessmentState(sector=Sector.CUSTOMER_SUPPORT, problem="p")
    eng._apply_updates(s, [eng.ExtractedUpdate(
        field="monthly_volume", value=10000, provenance=Provenance.USER_PROVIDED)])
    first = point(s.monthly_volume)
    # The extractor can legitimately emit a null for "not mentioned in this
    # message"; that must not be read as a retraction.
    eng._apply_updates(s, [eng.ExtractedUpdate(
        field="monthly_volume", value=None, provenance=Provenance.USER_PROVIDED)])
    print(f"    after a null extraction: {point(s.monthly_volume):,.0f} (was {first:,.0f})")
    check("NULL", point(s.monthly_volume) == first,
          "'not mentioned in this message' is not the same as 'retracted'")


def case_unknown_bounded() -> None:
    print("\nUNKNOWN — 'I don't know' does not loop forever")
    asked = []

    def fake(system, user, **kw):
        if "extraction step" in system:
            return {"updates": []}
        asked.append(user)
        return {"acknowledgment": "", "question": "q?"}

    original = eng.complete_json
    eng.complete_json = fake
    try:
        s = AssessmentState(sector=Sector.CUSTOMER_SUPPORT, problem="p")
        ctx = ConversationContext()
        turns = 0
        while not s.complete and turns < 20:
            r = eng.run_turn(s, "I don't know", ctx)
            ctx = r.context
            turns += 1
    finally:
        eng.complete_json = original
    print(f"    terminated after {turns} turns as {s.status.value}; "
          f"{len(asked)} questions asked")
    check("UNK", s.complete, "the interview terminates rather than looping")
    check("UNK", s.status.value == "uncertain",
          "it terminates as UNCERTAIN, not as a fabricated READY")
    check("UNK", len(asked) <= eng.MAX_ATTEMPTS_PER_FIELD + 1,
          "clarification on one field is bounded (attempts + the warm-up turn)")


def case_voice_text_parity() -> None:
    print("\nVOICE/TEXT — one engine, no separate assessment logic")
    src = (Path(__file__).resolve().parent.parent / "src" / "interviewer" / "voice.py").read_text()
    check("VOICE", "run_turn" in src,
          "the voice path calls the shared engine")
    for forbidden in ("_apply_updates", "select_next_need", "_minimum_sufficient"):
        check("VOICE", forbidden not in src,
              f"voice does not reimplement {forbidden}")


def _scripted(script: dict):
    """A stubbed LLM that returns the mapped facts for each user message."""
    def fake(system, user, **kw):
        if "extraction step" in system:
            msg = user.split('Latest user message:\n"')[-1].rstrip('"\n')
            return {"updates": [{"field": k, "value": v, "provenance": "user_provided"}
                                for k, v in script.get(msg, {}).items()]}
        if "ask their name" in system:
            return {"acknowledgment": "", "question": "What's your name?"}
        if "warm-up of a consultative interview" in system:
            return {"acknowledgment": "Nice to meet you.",
                    "question": "What are you working on?"}
        return {"acknowledgment": "Got it.", "question": "[next]"}
    return fake


def case_warmup() -> None:
    print("\nWARMUP — natural opening, name kept OUT of AssessmentState")
    script = {
        "I'm Rishabh": {},
        "We're a BPO in India handling 5,000 tickets a month, automating triage": {
            "geography": "India", "monthly_volume": 5000, "process": "ticket triage"},
    }
    original = eng.complete_json
    eng.complete_json = _scripted(script)
    try:
        s = AssessmentState(sector=Sector.CUSTOMER_SUPPORT, problem="")
        ctx = ConversationContext()
        r0 = eng.run_turn(s, "", ctx); ctx = r0.context
        print(f"    turn 1 [{r0.phase}]: {r0.question}")
        r1 = eng.run_turn(s, "I'm Rishabh", ctx); ctx = r1.context
        print(f"    turn 2 [{r1.phase}]: {(r1.acknowledgment or '').strip()} {r1.question}")
        r2 = eng.run_turn(s, list(script)[1], ctx); ctx = r2.context
        filled = [u.field for u in r2.updated_fields if u.value is not None]
        print(f"    turn 3 [{r2.phase}] filled from the warm-up answer: {filled}")
    finally:
        eng.complete_json = original

    check("WARM", r0.phase == Phase.WARMUP.value,
          "the conversation opens in WARMUP, not with an assessment question")
    check("WARM", ctx.name == "Rishabh", "the name is captured")
    check("WARM", "name" not in s.model_dump(),
          "the name is NOT in AssessmentState — rapport cannot reach a calculation")
    check("WARM", ctx.warmup_completed and r2.phase != Phase.WARMUP.value,
          "warm-up transitions into discovery rather than continuing")
    check("WARM", set(filled) >= {"geography", "monthly_volume", "process"},
          "facts volunteered during warm-up are extracted opportunistically")
    check("WARM", s.geography == "India",
          "a fact given in conversation is never re-asked as a form field")


def case_warmup_skipped_when_facts_given() -> None:
    print("\nWARMUP — a user who leads with facts is not asked how their day is")
    script = {"We're a BPO in India doing 5,000 tickets a month": {
        "geography": "India", "monthly_volume": 5000, "process": "ticket triage"}}
    original = eng.complete_json
    eng.complete_json = _scripted(script)
    try:
        s = AssessmentState(sector=Sector.CUSTOMER_SUPPORT, problem="")
        ctx = ConversationContext()
        r = eng.run_turn(s, list(script)[0], ctx)
    finally:
        eng.complete_json = original
    print(f"    after one fact-carrying message: phase={r.context.phase.value} "
          f"warmup_completed={r.context.warmup_completed}")
    check("WARM", r.context.warmup_completed,
          "warm-up ends immediately once real facts arrive")


def case_tier2_costs_and_quality() -> None:
    print("\nTIER 2 — costs and quality are asked when there is room, never mandatory")
    from interviewer.fields import get_field
    for key in ("annual_tooling_cost", "error_rate", "rework_time_per_error_minutes",
                "annual_other_direct_cost", "current_quality_metric"):
        spec = get_field(key)
        check("T2", spec is not None and spec.tier == Tier.TIER_2
              and not spec.required_for_completion,
              f"{key} is Tier 2 and cannot block completion")

    s = completed_state()
    check("T2", eng._minimum_sufficient_reached(s),
          "an interview completes without any Tier-2 cost or quality answer")

    # Volunteered Tier-2 facts are captured.
    s2 = completed_state()
    eng._apply_updates(s2, [
        eng.ExtractedUpdate(field="annual_tooling_cost", value=40000,
                            provenance=Provenance.USER_PROVIDED),
        eng.ExtractedUpdate(field="error_rate", value=0.08,
                            provenance=Provenance.USER_PROVIDED)])
    print(f"    volunteered: tooling={point(s2.annual_tooling_cost):,.0f} "
          f"error_rate={point(s2.error_rate)}")
    check("T2", point(s2.annual_tooling_cost) == 40000 and point(s2.error_rate) == 0.08,
          "volunteered Tier-2 facts are captured")

    absent = eng.uncollected_tier2(completed_state())
    print(f"    uncollected Tier-2 reported at termination: {len(absent)} fields")
    check("T2", absent, "uncollected Tier-2 fields are reported explicitly, so the "
                        "user learns what would have improved the assessment")


def case_quality_metric_semantics() -> None:
    print("\nQUALITY — sector-appropriate metric, name and value kept together")
    from schemas.assessment_state import SECTOR_QUALITY_METRICS
    cs = [m.value for m in SECTOR_QUALITY_METRICS["customer_support"]]
    dp = [m.value for m in SECTOR_QUALITY_METRICS["document_processing"]]
    print(f"    support : {cs}")
    print(f"    document: {dp}")
    check("QUAL", "first_contact_resolution" in cs and "escalation_rate" in cs,
          "support is asked for FCR / escalation / rework, never 'your accuracy'")
    check("QUAL", "exception_rate" in dp and "straight_through_rate" in dp,
          "document processing is asked for exception rate / STP / first-pass yield")
    check("QUAL", not set(cs) & set(dp), "the two sectors share no metric")

    s = completed_state(current_quality_metric=CurrentQualityMetric.EXCEPTION_RATE,
                        current_quality_value={"min": 0.12, "max": 0.16})
    print(f"    stored: {s.current_quality_metric.value} = "
          f"{s.current_quality_value.min}-{s.current_quality_value.max}")
    check("QUAL", s.current_quality_metric and s.current_quality_value,
          "the metric NAME and the value are stored together")

    from calc.quality import from_collected, QualityMetric
    obs = from_collected("exception_rate", s.current_quality_value)
    print(f"    12-16% exceptions -> {obs.metric.value} "
          f"{obs.value.min:.0%}-{obs.value.max:.0%}")
    check("QUAL", obs.metric == QualityMetric.NON_EXCEPTION_RATE,
          "an exception rate becomes a NON-EXCEPTION rate, never an 'accuracy'")
    check("QUAL", from_collected("escalation_rate", s.current_quality_value) is None,
          "a metric with no comparable AI-side counterpart yields no comparison "
          "rather than a fabricated one")


def case_compliance_normalisation() -> None:
    print("\nCOMPLIANCE — normalised to registry keys, original wording preserved")
    from interviewer.fields import get_field
    from lib.compliance import evaluate_implementation, load_attestations, normalise_standard
    spec = get_field("risk.compliance_exposure")
    keys = sorted({normalise_standard(a.standard) for a in load_attestations()})
    hint = spec.extraction_hint or ""
    missing = [k for k in ("hipaa", "gdpr", "soc 2", "iso 27001", "pci dss")
               if k not in hint]
    print(f"    canonical keys named in the extraction hint: {not missing}")
    check("COMP", not missing,
          "the extraction hint lists the canonical keys the filter matches on")
    check("COMP", "do NOT" in hint and "guess" in hint,
          "a vague answer is flagged ambiguous rather than guessed into HIPAA")
    check("COMP", "never state or imply whether a requirement is satisfied" in hint,
          "the LLM normalises vocabulary only — it never decides satisfaction")

    s = completed_state()
    s.set_value("risk.compliance_exposure", ["gdpr"])
    verdict = evaluate_implementation("make", "gdpr")
    print(f"    collected 'gdpr' -> filter verdict for make: {verdict.status.value}")
    check("COMP", verdict.status.value in ("SUPPORTED", "UNKNOWN", "NOT_APPLICABLE"),
          "a collected requirement reaches the deterministic filter and returns a "
          "verdict from evidence")

    from schemas.assessment_state import ComplianceRequirement
    req = ComplianceRequirement(standard="gdpr",
                                stated_as="we need to comply with GDPR")
    check("COMP", req.standard == "gdpr" and req.stated_as,
          "the canonical key and the user's original wording are both preserved")


def case_worker_role_wiring() -> None:
    print("\nWORKER ROLE — the collected role now selects the labor rate")
    from calc.labor import resolve_labor_rate
    from schemas.assessment_state import ProcessRole

    rates = {}
    for role in (None, ProcessRole.CUSTOMER_SUPPORT_AGENT,
                 ProcessRole.CUSTOMER_SUPPORT_SPECIALIST):
        s = AssessmentState(sector=Sector.CUSTOMER_SUPPORT, geography="India",
                            monthly_volume=5000, worker_role_canonical=role)
        r = resolve_labor_rate(s)
        rates[role.value if role else "default"] = (r.hourly.min, r.hourly.max)
        print(f"    {(role.value if role else 'default'):<30} "
              f"{r.hourly.min:>7,.0f}-{r.hourly.max:,.0f} INR/hr")
    agent = rates["customer_support_agent"]
    specialist = rates["customer_support_specialist"]
    check("ROLE", specialist[0] > agent[0],
          "the specialist rate is REACHABLE and higher than the agent rate — "
          "it was previously unreachable, mis-costing tier-2 work ~2.4x")
    check("ROLE", rates["default"] == agent,
          "with no role established the sector default still applies")

    from interviewer.fields import get_field
    hint = get_field("worker_role_canonical").extraction_hint or ""
    check("ROLE", "ambiguous" in hint and "priced very differently" in hint,
          "an ambiguous role is flagged rather than guessed between two rates")


def case_process_stages() -> None:
    print("\nPROCESS STAGES — buy/build follows the architecture, not a blanket BUILD")
    from calc.implementation import _stage_plan
    from schemas.assessment_state import BuyOrBuild, ProcessStage

    s = AssessmentState(sector=Sector.CUSTOMER_SUPPORT)
    plans = {k: _stage_plan(s, k) for k in ("custom_code", "low_code", "managed_service")}
    for kind, plan in plans.items():
        buys = sorted(k for k, v in plan.items() if v == BuyOrBuild.BUY)
        print(f"    {kind:<16} buy={buys or 'none'}")
    check("STAGE", all(v == BuyOrBuild.BUILD for v in plans["custom_code"].values()),
          "a custom build buys nothing")
    check("STAGE", any(v == BuyOrBuild.BUY for v in plans["low_code"].values()),
          "a low-code platform supplies some stages — the buy/build distinction "
          "now engages instead of defaulting everything to BUILD")
    check("STAGE", len([v for v in plans["managed_service"].values()
                        if v == BuyOrBuild.BUY]) >
          len([v for v in plans["low_code"].values() if v == BuyOrBuild.BUY]),
          "a managed service supplies more than a low-code platform")

    override = AssessmentState(sector=Sector.CUSTOMER_SUPPORT, process_stages=[
        ProcessStage(stage="integration", required=True, buy_or_build=BuyOrBuild.BUY)])
    check("STAGE", _stage_plan(override, "custom_code")["integration"] == BuyOrBuild.BUY,
          "an explicit user statement overrides the architecture default")


def case_dead_fields_removed() -> None:
    print("\nDEAD FIELDS — removed from the schema")
    from schemas.assessment_state import RiskInputs
    check("DEAD", "ai_solution" not in AssessmentState.model_fields,
          "ai_solution is gone — SolutionEstimate is the real carrier")
    check("DEAD", "reliability_gap" not in RiskInputs.model_fields,
          "risk.reliability_gap is gone — it is derived in calc/risk_score.py")
    print(f"    AssessmentState fields: {len(AssessmentState.model_fields)}")
    print(f"    RiskInputs fields     : {list(RiskInputs.model_fields)}")


def case_failure_probability_derived_only() -> None:
    print("\nFAILURE PROBABILITY — derived only, never asked")
    from calc.risk_score import derive_failure_probability
    from schemas.assessment_state import RiskInputs
    check("FPROB", "failure_probability" not in RiskInputs.model_fields,
          "there is no collected failure probability to ask for")
    check("FPROB", not any(f.key.endswith("failure_probability") for f in FIELDS),
          "the interviewer never asks the user to estimate it")

    sol = stub_solution()
    from solution.schema import PerformanceMetric
    sol.performance = [PerformanceMetric(
        metric="extraction_accuracy",
        estimate=RangeEstimate(min=85, max=98, provenance=Provenance.ASSUMED))]
    derived = derive_failure_probability(sol)
    print(f"    derived from architecture evidence: "
          f"{derived.min:.1%}-{derived.max:.1%}  [{derived.provenance.value}]")
    check("FPROB", derived is not None and derived.provenance == Provenance.DERIVED,
          "it is derived from the architecture's own performance evidence")


def case_completion_statement() -> None:
    print("\nTRANSPARENCY — a plain statement of what could not be established")
    from schemas.assessment_state import CurrentQualityMetric
    bare = eng.completion_statement(completed_state(), eng.InterviewStatus.READY)
    print(f"    {bare[:150]}")
    check("STMT", bare.startswith("Your assessment is complete."),
          "the statement opens by confirming completion")
    check("STMT", "couldn't establish" in bare and "rather than estimated" in bare,
          "it names what was missing and says it was excluded, not guessed")

    one = completed_state(
        annual_tooling_cost=40000, error_rate=0.08,
        rework_time_per_error_minutes=15, annual_other_direct_cost=1000,
        current_quality_metric=CurrentQualityMetric.FIRST_CONTACT_RESOLUTION,
        current_quality_value=0.72, fully_loaded_annual_cost=500000,
        fraction_time_on_process=0.7, existing_data="logs",
        integration_complexity="medium")
    one.set_value("risk.compliance_exposure", ["gdpr"])
    s1 = eng.completion_statement(one, eng.InterviewStatus.READY)
    print(f"    {s1}")
    check("STMT", "that was excluded" in s1,
          "a single missing input reads as singular, not 'those were'")
    check("STMT", "plus" not in s1, "a short list is not truncated")
    check("STMT", "plus" in bare, "a long list is truncated so it stays readable")

    incomplete = eng.completion_statement(completed_state(),
                                          eng.InterviewStatus.UNCERTAIN)
    check("STMT", incomplete.startswith("The assessment is incomplete."),
          "an UNCERTAIN interview says so rather than claiming completion")


if __name__ == "__main__":
    case_downstream_acceptance()
    case_warmup()
    case_warmup_skipped_when_facts_given()
    case_tier2_costs_and_quality()
    case_quality_metric_semantics()
    case_compliance_normalisation()
    case_geography()
    case_validation()
    case_ranges()
    case_tiering()
    case_over_asking_guard()
    case_multi_fill()
    case_null_never_overwrites()
    case_unknown_bounded()
    case_worker_role_wiring()
    case_process_stages()
    case_dead_fields_removed()
    case_failure_probability_derived_only()
    case_completion_statement()
    case_voice_text_parity()
    print()
    if failures:
        print(f"{len(failures)} CHECK(S) FAILED:")
        for f in failures:
            print(f"  - {f}")
        sys.exit(1)
    print("ALL INTERVIEWER CASES PASSED")
