"""Acceptance tests for Alternatives — deployIQ_MVP.txt section 11.

Each case is named for the clause it defends. The LLM is stubbed throughout,
so this runs with no API key and is fully deterministic.
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
    AssessmentState, DataReadiness, ImpactSeverity, RiskInputs, Sector,
)
from solution import alternatives, capabilities as caps_mod, estimator, patterns
from solution.schema import (
    AlternativeSource, Capability, DifferenceKind, ImplementationKind,
)
import llm.openai_client as oc

failures: list[str] = []


def check(case: str, cond: bool, desc: str) -> None:
    print(f"    [{'PASS' if cond else 'FAIL'}] {desc}")
    if not cond:
        failures.append(f"{case}: {desc}")


# --- fixtures --------------------------------------------------------------

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


def install(caps, tasks, explanations=None):
    """Stub the decomposition, task estimates and (optionally) explanations."""
    def fake(system, user, **kw):
        if "decompose" in system:
            return {"capabilities": caps}
        if "alternative" in system.lower():
            return {"explanations": explanations or []}
        return {"tasks": tasks}
    caps_mod.complete_json = fake
    oc.complete_json = fake


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


def doc_run(state=None, tasks=None, explanations=None, explain=False):
    install(DOC_CAPS, tasks or DOC_TASKS, explanations)
    st = state or doc_state()
    est = estimator.estimate(st)
    return st, est, alternatives.derive(st, est, explain=explain)


# --- 11.4 the primary selection is untouched -------------------------------

def case_A_primary_untouched() -> None:
    print("\nA — 11.4: alternatives do not override, modify or re-rank the primary")
    st, est, res = doc_run()
    before = (est.recommended_pattern, est.recommended_implementation,
              est.overall_automation.min, est.overall_automation.max,
              list(est.candidate_implementations))
    alternatives.derive(st, est, explain=False)
    after = (est.recommended_pattern, est.recommended_implementation,
             est.overall_automation.min, est.overall_automation.max,
             list(est.candidate_implementations))
    check("A", before == after, "deriving alternatives leaves the estimate byte-identical")

    primary_impl = next(i for i in patterns.pattern(est.recommended_pattern).implementations
                        if i.id == est.recommended_implementation)
    key = (est.recommended_pattern, primary_impl.kind)
    check("A", all((a.pattern_id, a.implementation_kind) != key
                   for a in res.alternatives),
          "the primary architecture+implementation model is never offered back "
          "as an alternative")
    check("A", res.is_recommendation is False,
          "the payload declares itself non-recommending (11.6)")
    check("A", res.economics_included is False,
          "no economic model is attached to alternatives (11.8)")
    check("A", "not a ranking of preference" in res.ordering_basis,
          "display order is documented as order, not preference")


# --- 11.1 materially different ---------------------------------------------

def case_B_materiality() -> None:
    print("\nB — 11.1: alternatives must be materially different")
    st, est, res = doc_run()
    ids = [a.id for a in res.alternatives]
    print(f"    primary={est.recommended_pattern}/{est.recommended_implementation}")
    for a in res.alternatives:
        print(f"      {a.id:42} {a.difference_kind.value}")

    keys = [(a.pattern_id, a.implementation_kind) for a in res.alternatives
            if a.source == AlternativeSource.REGISTRY]
    check("B", len(keys) == len(set(keys)),
          "no two alternatives share an architecture AND an implementation model")

    # n8n / Make / Zapier are all low-code builds of the same pattern. At most
    # one may occupy a slot; the rest must be recorded as vendor variants.
    vendor_variants = [r for r in res.rejected if "different vendor" in r.reason]
    check("B", any(r.implementation_id == "make" for r in vendor_variants),
          "a same-pattern same-kind vendor swap is rejected as a vendor variant, "
          "not surfaced as a second alternative")
    check("B", all(r.implementation_id not in ids for r in res.rejected),
          "nothing appears as both rejected and surfaced")


def case_C_implementation_model_counts() -> None:
    print("\nC — 11.2: a different implementation model of the same architecture "
          "is a real alternative")
    st, est, res = doc_run()
    same_arch = [a for a in res.alternatives
                 if a.difference_kind == DifferenceKind.IMPLEMENTATION_MODEL]
    check("C", bool(same_arch),
          "the custom build of the selected architecture is surfaced, not "
          "collapsed into the low-code one")
    if same_arch:
        a = same_arch[0]
        print(f"    {a.id}: {a.difference_from_primary}")
        check("C", a.pattern_id == est.recommended_pattern
              and a.implementation_kind != ImplementationKind.LOW_CODE,
              "it is the same pattern under a different implementation kind")


# --- 11.1 hard constraints apply to alternatives too -----------------------

def case_D_compliance_is_a_hard_filter() -> None:
    print("\nD — 11.1: alternatives satisfy the same hard compliance filter")
    st = doc_state(risk=RiskInputs(failure_impact="wrong payment",
                                   failure_impact_severity=ImpactSeverity.MODERATE,
                                   compliance_exposure=["gdpr"]))
    _, est, res = doc_run(state=st)
    print(f"    primary={est.recommended_pattern}/{est.recommended_implementation}")
    print(f"    surfaced: {[a.id for a in res.alternatives]}")
    excluded = [r for r in res.rejected if "hard compliance filter" in r.reason]
    check("D", bool(excluded),
          "candidates without GDPR evidence are excluded, with the reason recorded")
    from solution import ranking
    check("D", all(ranking.covers_compliance_by_evidence(a.implementation_id, ["gdpr"])
                   for a in res.alternatives
                   if a.source == AlternativeSource.REGISTRY),
          "every surfaced alternative carries evidence-backed GDPR support")


def case_E_never_padded() -> None:
    print("\nE — 11.1: never fabricate alternatives to reach a target count")
    st = doc_state(risk=RiskInputs(failure_impact="wrong payment",
                                   failure_impact_severity=ImpactSeverity.MODERATE,
                                   compliance_exposure=["gdpr"]))
    _, est, res = doc_run(state=st)
    registry_alts = [a for a in res.alternatives
                     if a.source == AlternativeSource.REGISTRY]
    print(f"    {len(registry_alts)} registry alternative(s) survived a GDPR constraint")
    check("E", len(registry_alts) < 2,
          "fewer than two are shown when fewer than two survive the constraints")
    check("E", len(res.alternatives) <= alternatives.MAX_ALTERNATIVES + 1,
          f"the count never exceeds the {alternatives.MAX_ALTERNATIVES}-alternative "
          f"ceiling plus the current-process baseline")


def case_F_no_primary_no_alternatives() -> None:
    print("\nF — 11.7: no credible alternative -> a statement, not a filler list")
    st = doc_state(monthly_volume=None)          # forces a refusal upstream
    install(DOC_CAPS, DOC_TASKS)
    est = estimator.estimate(st)
    res = alternatives.derive(st, est, explain=False)
    check("F", est.recommended_pattern == "", "the estimator refused, as set up")
    check("F", res.alternatives == [], "no alternatives are invented without a primary")
    check("F", bool(res.statement), "a statement explains the absence")
    print(f"    {res.statement}")


# --- 11.5 the LLM boundary --------------------------------------------------

def case_G_llm_cannot_invent_an_alternative() -> None:
    print("\nG — 11.5: the LLM may not invent an alternative outside the registry")
    _, est, res = doc_run(
        explanations=[
            {"id": "blockchain_ledger_pipeline",
             "text": "A distributed ledger would also work here."},
        ], explain=True)
    ids = {a.id for a in res.alternatives}
    check("G", "blockchain_ledger_pipeline" not in ids,
          "an id the model invented never becomes an alternative")
    check("G", any("not one of the selected alternatives" in n
                   for n in res.llm_guard_notes),
          "the discard is reported rather than silently swallowed")


def case_H_llm_cannot_assert_figures() -> None:
    print("\nH — 11.5: the LLM may not claim benchmarks, costs or effort")
    _, est, res = doc_run()
    target = res.alternatives[0].id
    _, est, res = doc_run(
        explanations=[{"id": target,
                       "text": ("This approach keeps the pipeline in one place. "
                                "It typically reaches 95% accuracy and costs "
                                "$40,000 to build. It suits teams that already "
                                "run their own infrastructure.")}],
        explain=True)
    alt = next(a for a in res.alternatives if a.id == target)
    print(f"    kept: {alt.explanation!r}")
    check("H", "95" not in alt.explanation and "40,000" not in alt.explanation,
          "the sentence carrying invented figures is stripped")
    check("H", "keeps the pipeline in one place" in alt.explanation
          and "own infrastructure" in alt.explanation,
          "the surrounding explanation survives — a bad sentence costs a "
          "sentence, not the whole answer")
    check("H", any("figure" in n for n in res.llm_guard_notes),
          "the strip is recorded in the guard notes")


def case_I_llm_cannot_recommend() -> None:
    print("\nI — 11.6: surfacing an alternative is a fact, not a nudge")
    _, est, res = doc_run()
    target = res.alternatives[0].id
    _, est, res = doc_run(
        explanations=[{"id": target,
                       "text": ("You should use this instead of the selected "
                                "solution. It is the best choice for your team. "
                                "It may be preferable when the workload has to "
                                "stay on your own hardware.")}],
        explain=True)
    alt = next(a for a in res.alternatives if a.id == target)
    print(f"    kept: {alt.explanation!r}")
    check("I", "should" not in alt.explanation.lower()
          and "best choice" not in alt.explanation.lower(),
          "directive recommendation language is removed")
    check("I", "may be preferable when" in alt.explanation,
          "CONDITIONAL preference survives — 11.3 explicitly asks for it")


def case_J_deterministic_half_needs_no_llm() -> None:
    print("\nJ — 11.1: candidate selection is deterministic, before any LLM call")
    install(DOC_CAPS, DOC_TASKS)
    st = doc_state()
    est = estimator.estimate(st)

    calls = {"n": 0}
    def exploding(system, user, **kw):
        calls["n"] += 1
        raise AssertionError("the LLM must not be consulted to select alternatives")
    oc.complete_json = exploding
    res = alternatives.derive(st, est, explain=False)
    check("J", calls["n"] == 0,
          "with explanation disabled, no LLM call is made at all")
    check("J", bool(res.alternatives),
          "alternatives are still produced from the registry alone")

    # And a failing LLM degrades the prose, never the selection.
    n_deterministic = len(res.alternatives)
    def failing(system, user, **kw):
        if "alternative" in system.lower():
            raise RuntimeError("no API key")
        return {"capabilities": DOC_CAPS} if "decompose" in system else {"tasks": DOC_TASKS}
    oc.complete_json = failing
    res2 = alternatives.derive(st, est, explain=True)
    check("J", len(res2.alternatives) == n_deterministic,
          "an LLM failure loses the explanations, never an alternative")
    check("J", all(a.explanation == "" for a in res2.alternatives)
          and any("unavailable" in n or "failed" in n for n in res2.llm_guard_notes),
          "the missing explanation is reported rather than left ambiguous")


# --- 11.3 the comparison ----------------------------------------------------

def case_K_comparison_is_registry_backed() -> None:
    print("\nK — 11.3: every comparison axis is present and registry/code derived")
    _, est, res = doc_run()
    for a in [x for x in res.alternatives if x.source == AlternativeSource.REGISTRY]:
        c = a.comparison
        impl = next(i for i in patterns.pattern(a.pattern_id).implementations
                    if i.id == a.implementation_id)
        check("K", c.approach == patterns.pattern(a.pattern_id).architecture,
              f"{a.id}: approach is the registry's own architecture text")
        check("K", set(impl.compatibility.strengths) <= set(c.strengths),
              f"{a.id}: strengths come from registry metadata")
        check("K", set(impl.compatibility.limitations) <= set(c.limitations),
              f"{a.id}: limitations come from registry metadata")
        check("K", c.implementation_complexity is not None and c.risks,
              f"{a.id}: complexity band and risks are populated")
        check("K", "no per-task automation estimate" in " ".join(a.uncertainties),
              f"{a.id}: the absence of a per-alternative estimate is stated (11.8)")


def case_L_complexity_uses_the_shared_scope_model() -> None:
    print("\nL — 11.3: implementation complexity reuses the primary's scope model")
    from solution import scope
    st, est, res = doc_run()
    caps = [Capability(c) for c in DOC_CAPS]
    for a in [x for x in res.alternatives if x.source == AlternativeSource.REGISTRY]:
        expected = scope.effort_scope(st, caps, hitl_modes=None,
                                      implementation_kind=a.implementation_kind).band
        check("L", a.comparison.implementation_complexity == expected,
              f"{a.id}: band matches solution.scope exactly ({expected.value})")


def case_M_uncertainty_is_surfaced() -> None:
    print("\nM — 11.7: an alternative resting on an assumption says so")
    _, est, res = doc_run()
    for a in [x for x in res.alternatives if x.source == AlternativeSource.REGISTRY]:
        joined = " ".join(a.uncertainties)
        has_assumption = any(m.estimate.provenance.value != "sourced"
                             for m in a.comparison.expected_automation)
        check("M", (not has_assumption) or "is an assumption" in joined,
              f"{a.id}: assumed performance metrics are declared as assumptions")
    marginal = [a for a in res.alternatives if not a.comparison.when_preferable]
    check("M", all("could be established from registry metadata" in " ".join(a.uncertainties)
                   for a in marginal),
          "an alternative with no demonstrable advantage says so instead of "
          "leaving the field silently blank")


def case_N_when_preferable_is_grounded() -> None:
    print("\nN — 11.3/11.5: 'when preferable' is derived, never invented prose")
    _, est, res = doc_run()
    for a in res.alternatives:
        for w in a.comparison.when_preferable:
            print(f"    {a.id[:34]:34} {w[:74]}")
    grounded = [w for a in res.alternatives for w in a.comparison.when_preferable]
    check("N", all(not any(ch.isdigit() for ch in w) for w in grounded),
          "no 'when preferable' entry asserts a figure")


# --- 11.2 categories --------------------------------------------------------

def case_O_registry_gaps_are_named() -> None:
    print("\nO — 11.2: categories the registry cannot supply are named as gaps")
    _, est, res = doc_run()
    for c in res.categories_not_in_registry:
        print(f"    not in registry: {c}")
    check("O", len(res.categories_not_in_registry) >= 3,
          "the uncovered 11.2 categories are reported rather than silently omitted")
    check("O", all(c not in [a.name for a in res.alternatives]
                   for c in res.categories_not_in_registry),
          "a named gap is never turned into an alternative to fill the slot")


def case_P_status_quo_is_gated() -> None:
    print("\nP — 11.2: the current process appears only where it is a meaningful "
          "baseline")
    _, strong, res_strong = doc_run()
    has_sq = lambda r: any(a.source == AlternativeSource.CURRENT_PROCESS
                           for a in r.alternatives)
    print(f"    strong case: automation {strong.overall_automation.min}-"
          f"{strong.overall_automation.max}%, status quo shown = {has_sq(res_strong)}")
    check("P", not has_sq(res_strong),
          "a solid AI case does not carry 'do nothing' as an alternative")

    weak_tasks = [dict(t, automation_min=5, automation_max=15) for t in DOC_TASKS]
    st_weak, weak, res_weak = doc_run(tasks=weak_tasks)
    print(f"    weak case:   automation {weak.overall_automation.min}-"
          f"{weak.overall_automation.max}%, status quo shown = {has_sq(res_weak)}")
    check("P", has_sq(res_weak),
          "a weak AI case does surface continuing unchanged as a live option")
    if has_sq(res_weak):
        sq = next(a for a in res_weak.alternatives
                  if a.source == AlternativeSource.CURRENT_PROCESS)
        check("P", sq.pattern_id == "" and sq.implementation_id == "",
              "the current-process baseline claims no registry entry")
        check("P", sq.comparison.approach == st_weak.process.strip(),
              "its description is the user's own process text from "
              "AssessmentState, verbatim")
        check("P", "automate invoice processing" in " ".join(sq.comparison.limitations),
              "its limitation quotes the user's own stated problem, not an "
              "invented drawback")


def case_Q_metadata_sufficiency() -> None:
    print("\nQ — 11.1: a candidate without comparable registry metadata is dropped")
    from solution.schema import Compatibility, ImplementationOption
    from schemas.assessment_state import EffortBand
    bare = ImplementationOption(
        id="bare", name="Undocumented build", kind=ImplementationKind.CUSTOM_CODE,
        compatibility=Compatibility(supported_capabilities=[Capability.INGEST],
                                    technical_complexity=EffortBand.SMALL))
    pat = patterns.pattern("document_pipeline")
    check("Q", alternatives._has_sufficient_metadata(pat, bare) is not None,
          "an implementation declaring no strengths/limitations is rejected")
    print(f"    reason: {alternatives._has_sufficient_metadata(pat, bare)}")
    good = next(i for i in pat.implementations if i.id == "custom_docpipe")
    check("Q", alternatives._has_sufficient_metadata(pat, good) is None,
          "a fully documented implementation passes")



# --- 11.2 the deterministic alternative -------------------------------------

TRIAGE_CAPS = ["ingest", "classify", "route", "human_escalate"]
TRIAGE_TASKS = [
    {"task": "receive ticket", "capability": "ingest",
     "automation_min": 90, "automation_max": 98,
     "handling_time_min_minutes": 1, "handling_time_max_minutes": 1,
     "confidence": "high", "hitl": "autonomous", "rationale": "mailbox connector"},
    {"task": "tag by product keyword", "capability": "classify",
     "automation_min": 70, "automation_max": 85,
     "handling_time_min_minutes": 2, "handling_time_max_minutes": 2,
     "confidence": "medium", "hitl": "ai_assisted", "rationale": "keyword rules"},
    {"task": "route to queue", "capability": "route",
     "automation_min": 85, "automation_max": 95,
     "handling_time_min_minutes": 1, "handling_time_max_minutes": 1,
     "confidence": "high", "hitl": "ai_assisted", "rationale": "lookup table"},
    {"task": "escalate priority cases", "capability": "human_escalate",
     "automation_min": 10, "automation_max": 20,
     "handling_time_min_minutes": 1, "handling_time_max_minutes": 1,
     "confidence": "low", "hitl": "escalation", "rationale": "SLA breach"},
]


def triage_state() -> AssessmentState:
    return AssessmentState(
        sector=Sector.CUSTOMER_SUPPORT, problem="tickets sit unrouted for hours",
        process="tickets arrive by email, an agent reads and assigns them to a queue",
        monthly_volume=9000, avg_time_per_unit_minutes=5, current_headcount=6,
        required_accuracy=0.9, data_readiness=DataReadiness.GOOD,
        current_tools=["Zendesk"], geography="India",
        risk=RiskInputs(failure_impact="delayed response",
                        failure_impact_severity=ImpactSeverity.MINOR))


def case_R_rules_alternative_surfaces() -> None:
    print("\nR — 11.2: a deterministic alternative is surfaced where no model "
          "is required")
    install(TRIAGE_CAPS, TRIAGE_TASKS)
    st = triage_state()
    est = estimator.estimate(st)
    res = alternatives.derive(st, est, explain=False)
    rules = [a for a in res.alternatives if a.pattern_id == "rules_based_workflow"]
    print(f"    primary={est.recommended_pattern}/{est.recommended_implementation}")
    print(f"    surfaced: {[a.id for a in res.alternatives]}")
    check("R", bool(rules),
          "a triage workflow (ingest/classify/route/escalate) surfaces the "
          "rules-based alternative")
    if rules:
        a = rules[0]
        check("R", any("no model in the loop" in w
                       for w in a.comparison.when_preferable),
              "its decisive advantage — no model in the loop — is stated as a "
              "situation in which it may be preferable (11.6's own example)")
        impl = next(i for i in patterns.pattern(a.pattern_id).implementations
                    if i.id == a.implementation_id)
        check("R", impl.providers == [],
              "that claim is a registry fact: the implementation declares no "
              "model-bearing provider")


def case_S_rules_pattern_cannot_overreach() -> None:
    print("\nS — 11.1: the rules pattern declares only what rules can do")
    pat = patterns.pattern("rules_based_workflow")
    for impl in pat.implementations:
        declared = set(impl.compatibility.supported_capabilities)
        for prov in impl.providers:
            declared |= set(prov.compatibility.supported_capabilities)
        check("S", Capability.GENERATE not in declared,
              f"{impl.id}: claims no GENERATE — a ruleset cannot write language")
        check("S", Capability.EXTRACT not in declared,
              f"{impl.id}: claims no EXTRACT — a ruleset cannot read unstructured "
              f"documents")

    # And therefore it must NOT be a candidate for a workflow that needs those.
    install(DOC_CAPS, DOC_TASKS)
    st = doc_state()
    est = estimator.estimate(st)
    res = alternatives.derive(st, est, explain=False)
    check("S", all(a.pattern_id != "rules_based_workflow" for a in res.alternatives),
          "an extraction workflow is never offered a rules-based alternative")
    check("S", est.recommended_pattern == "document_pipeline",
          "and the primary selection is unchanged by the registry addition")


def case_T_rules_pattern_never_displaces_the_baseline() -> None:
    print("\nT — 11.4: adding a pattern does not disturb the primary selection")
    install(TRIAGE_CAPS, TRIAGE_TASKS)
    est = estimator.estimate(triage_state())
    check("T", est.recommended_pattern == "ai_assisted_workflow",
          "the curated sector baseline still wins for a support workflow "
          "(the rules pattern competes as an ordinary candidate, at reference "
          "alignment 0.2)")


def case_U_rules_compliance_composition() -> None:
    print("\nU — evidence: dropping the model drops it from the compliance "
          "composition")
    from lib.compliance import REGISTRY_TO_EVIDENCE, supported_standards
    check("U", "openai_api" not in REGISTRY_TO_EVIDENCE["n8n_rules"],
          "a rules build's evidence composition contains no model API")
    check("U", "openai_api" in REGISTRY_TO_EVIDENCE["n8n"],
          "the AI build's does")
    ai, rules = supported_standards("make"), supported_standards("make_rules")
    print(f"    make       -> {ai}")
    print(f"    make_rules -> {rules}")
    check("U", set(ai) <= set(rules),
          "removing a component never loses a standard the composition had")
    check("U", set(rules) - set(ai) != set(),
          "and it can gain one the model API was holding back (SOC 3)")
    check("U", supported_standards("n8n_rules") == [],
          "no evidence is invented for a self-hosted build that has none")


def case_V_status_quo_threshold_is_calibrated() -> None:
    print("\nV — the status-quo gate is a disclosable assumption, not a literal")
    from solution.calibration import ALTERNATIVES_CALIBRATION, all_calibration_params
    param = ALTERNATIVES_CALIBRATION.status_quo_automation_ceiling
    print(f"    {param.key} = {param.value} {param.unit} "
          f"({param.provenance.value}, v{param.version}, "
          f"reviewed {param.last_reviewed})")
    check("V", param.provenance.value == "assumed",
          "it is tagged as an assumption, not a measurement")
    check("V", bool(param.unit) and bool(param.rationale) and bool(param.last_reviewed),
          "unit, rationale and review date are all populated")
    check("V", param in all_calibration_params(),
          "it is disclosed alongside every other calibration in the module")
    check("V", alternatives.STATUS_QUO_CEILING is param,
          "alternatives reads the calibration object, keeping no second copy "
          "of the number")


def main() -> None:
    for fn in (case_A_primary_untouched, case_B_materiality,
               case_C_implementation_model_counts, case_D_compliance_is_a_hard_filter,
               case_E_never_padded, case_F_no_primary_no_alternatives,
               case_G_llm_cannot_invent_an_alternative, case_H_llm_cannot_assert_figures,
               case_I_llm_cannot_recommend, case_J_deterministic_half_needs_no_llm,
               case_K_comparison_is_registry_backed,
               case_L_complexity_uses_the_shared_scope_model,
               case_M_uncertainty_is_surfaced, case_N_when_preferable_is_grounded,
               case_O_registry_gaps_are_named, case_P_status_quo_is_gated,
               case_Q_metadata_sufficiency, case_R_rules_alternative_surfaces,
               case_S_rules_pattern_cannot_overreach,
               case_T_rules_pattern_never_displaces_the_baseline,
               case_U_rules_compliance_composition,
               case_V_status_quo_threshold_is_calibrated):
        fn()
    print("\n" + ("ALL ALTERNATIVES CASES PASSED" if not failures else
                  "FAILURES:\n  " + "\n  ".join(failures)))
    sys.exit(1 if failures else 0)


if __name__ == "__main__":
    main()
