"""P6 integration/orchestration checks.

Deterministic fixtures and LLM stubs only; no API key required.
"""

from __future__ import annotations

import sys
import types
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
if "openai" not in sys.modules:
    _s = types.ModuleType("openai"); _s.OpenAI = lambda **kw: None
    sys.modules["openai"] = _s

from calc.ai_state import LaborRealization
from calc.engine import EconomicInputError
from interviewer import engine as interviewer_engine
from interviewer.conversation import ConversationContext
from pipeline import orchestrate as orch
from report import validate
from report.schema import LaborRealizationSource, ReportMode
from schemas.assessment_state import RiskInputs
from scripts.report_cases import rng, solution, state
from solution import capabilities as caps_mod

failures: list[str] = []


def check(case: str, cond: bool, desc: str) -> None:
    print(f"    [{'PASS' if cond else 'FAIL'}] {desc}")
    if not cond:
        failures.append(f"{case}: {desc}")


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


def install_llm_stub() -> None:
    def fake(system, user, **kw):
        s = str(system).lower()
        if "decompose" in s:
            return {"capabilities": DOC_CAPS}
        if "estimate automation per workflow task" in s:
            return {"tasks": DOC_TASKS}
        if "explaining pre-selected alternative approaches" in s:
            return {"explanations": []}
        return {}

    caps_mod.complete_json = fake
    interviewer_engine.complete_json = fake
    import llm.openai_client as oc
    oc.complete_json = fake


def _base_run(**state_kw):
    install_llm_stub()
    orig = orch.estimator.estimate
    try:
        orch.estimator.estimate = lambda _state: solution()
        return orch.run_assessment(
            state(**state_kw),
            labor_realization=LaborRealization.COST_ELIMINATED,
            labor_realization_source=LaborRealizationSource.USER,
            enable_narration=False,
        )
    finally:
        orch.estimator.estimate = orig


def case_A_B_I_J_K_L_M_N_V() -> None:
    print("\nA/B/I/J/K/L/M/N/V — full e2e + determinism + canonical bundle checks")
    st = state()
    before = st.model_dump(mode="json")
    run1 = _base_run()
    run2 = _base_run()

    check("A", run1.final_report.mode is ReportMode.FULL, "full end-to-end run produces FULL")
    check("B", run1.rendered.markdown == run2.rendered.markdown,
          "repeated deterministic execution is byte-identical")
    check("I", run1.bundle.economics == run1.drivers.scores.result,
          "canonical EconomicResult equals the one used by scoring")
    check("J", run1.bundle.scores == run1.drivers.scores,
          "ReportInput carries canonical scores bundle")
    check("K", "should build" not in run1.rendered.markdown.lower(),
          "scoring output does not introduce verdict language")
    keys1 = [d.key for d in run1.drivers.drivers]
    keys2 = [d.key for d in run2.drivers.drivers]
    check("L", keys1 == keys2, "driver ordering is deterministic")
    check("M", "not a ranking of preference" in run1.alternatives.ordering_basis.lower(),
          "alternatives stay informational")
    check("N", "recalculation only" in (run1.sensitivity.note or "").lower(),
          "sensitivity stays informational")
    check("V", st.model_dump(mode="json") == before,
          "AssessmentState is not mutated with downstream stage objects")


def case_C_D_AA_AC() -> None:
    print("\nC/D/AA/AC — refusal handling + legit monetary facts")
    install_llm_stub()
    orig = orch.estimator.estimate
    try:
        ref = solution(recommended_pattern="", overall_automation=rng(0, 0))

        def refuse(_state):
            return ref

        orch.estimator.estimate = refuse
        run = orch.run_assessment(
            state(),
            labor_realization=LaborRealization.COST_ELIMINATED,
            labor_realization_source=LaborRealizationSource.USER,
            enable_narration=False,
        )
        check("C", run.final_report.mode is ReportMode.REFUSED,
              "estimator refusal yields refused report")
        check("AA", run.bundle.economics is None and run.bundle.drivers is None,
              "refused run carries no fabricated economics/scores/drivers")

        money_keys = [f.key for s in run.final_report.sections for f in s.figures
                      if f.unit.value == "money" and f.status.value == "known"]
        check("AC", any(k == "problem.loaded_cost" for k in money_keys),
              "legitimate assessment money facts can exist in refused mode")

        comp = solution(recommended_pattern="", overall_automation=rng(0, 0))
        comp.compliance_gap = True
        comp.compliance_statement = "hard compliance requirement unsatisfied"

        def comp_refuse(_state):
            return comp

        orch.estimator.estimate = comp_refuse
        run2 = orch.run_assessment(
            state(),
            labor_realization=LaborRealization.COST_ELIMINATED,
            labor_realization_source=LaborRealizationSource.USER,
            enable_narration=False,
        )
        check("D", run2.final_report.mode is ReportMode.REFUSED,
              "compliance refusal yields refused report")
    finally:
        orch.estimator.estimate = orig


def case_E_F_G_H_Y_Z_AD() -> None:
    print("\nE/F/G/H/Y/Z/AD — partials, currency, labor policy, divergence")
    install_llm_stub()

    # E partial economics via engine-input failure surfaced through rank_drivers.
    orig_rank = orch.driver_ranking.rank_drivers
    try:
        def boom(*a, **k):
            raise EconomicInputError(["economic engine could not run"])

        orch.driver_ranking.rank_drivers = boom
        run = orch.run_assessment(
            state(),
            labor_realization=LaborRealization.COST_ELIMINATED,
            labor_realization_source=LaborRealizationSource.USER,
            enable_narration=False,
        )
        check("E", run.final_report.mode is ReportMode.PARTIAL,
              "engine-input error yields PARTIAL")
        check("AD", bool(run.bundle.economic_error), "partial carries explicit gaps/reasons")
    finally:
        orch.driver_ranking.rank_drivers = orig_rank

    run_u = _base_run(geography=None)
    money = [f for s in run_u.final_report.sections for f in s.figures
             if f.unit.value == "money" and f.status.value == "known"]
    check("F", money and all(f.currency is None for f in money),
          "unresolved currency is preserved without silent fallback")
    check("Y", "currency unresolved" in run_u.rendered.markdown.lower(),
          "geography/currency never silently fallback")

    run_missing = orch.run_assessment(
        state(),
        labor_realization=None,
        labor_realization_source=LaborRealizationSource.UNSET,
        enable_narration=False,
    )
    check("G", run_missing.final_report.mode is ReportMode.PARTIAL,
          "missing LaborRealization yields partial/incomplete state")
    check("Z", run_missing.bundle.labor_realization is None
          and run_missing.bundle.labor_realization_source is LaborRealizationSource.UNSET,
          "LaborRealization is never defaulted")

    run_div = _base_run(current_headcount=rng(4, 4), avg_time_per_unit_minutes=rng(30, 30),
                        monthly_volume=rng(20000, 20000), fraction_time_on_process=0.9)
    check("H", run_div.drivers.scores.result.labor_consistency.status.value == "divergent",
          "divergent labor formulation is preserved")


def case_O_P_Q_R_S_T_U() -> None:
    print("\nO/P/Q/R/S/T/U — validation order, narration fallback, final renderer")
    install_llm_stub()

    order: list[str] = []
    orig_validate = orch.validate.validate
    orig_narrate = orch.narrate_mod.narrate

    def wrapped_validate(*a, **k):
        order.append("validate")
        return orig_validate(*a, **k)

    def wrapped_narrate(*a, **k):
        order.append("narrate")
        return orig_narrate(*a, **k)

    orch.validate.validate = wrapped_validate
    orch.narrate_mod.narrate = wrapped_narrate
    try:
        def no_llm(*a, **k):
            raise RuntimeError("unavailable")

        run = orch.run_assessment(
            state(),
            labor_realization=LaborRealization.COST_ELIMINATED,
            labor_realization_source=LaborRealizationSource.USER,
            enable_narration=True,
            narration_complete_json=no_llm,
        )
        check("O", order and order[0] == "validate" and "narrate" in order,
              "validation happens before narration")
        check("Q", not run.used_narration, "LLM unavailable falls back deterministically")

        base = orch.run_assessment(
            state(),
            labor_realization=LaborRealization.COST_ELIMINATED,
            labor_realization_source=LaborRealizationSource.USER,
            enable_narration=False,
        )
        check("S", run.final_report.model_dump() == base.final_report.model_dump(),
              "narration failure returns exact deterministic fallback")
    finally:
        orch.validate.validate = orig_validate
        orch.narrate_mod.narrate = orig_narrate

    # P: invalid deterministic report never reaches narration.
    orig_validate = orch.validate.validate
    orig_narrate = orch.narrate_mod.narrate
    called = {"narrate": 0}
    try:
        def invalid_once(*a, **k):
            r = orig_validate(*a, **k)
            if called["narrate"] == 0:
                r.add_error("forced", "forced")
            return r
        def count_narrate(*a, **k):
            called["narrate"] += 1
            return orig_narrate(*a, **k)

        orch.validate.validate = invalid_once
        orch.narrate_mod.narrate = count_narrate
        blocked = False
        try:
            orch.run_assessment(
                state(),
                labor_realization=LaborRealization.COST_ELIMINATED,
                labor_realization_source=LaborRealizationSource.USER,
                enable_narration=True,
            )
        except ValueError:
            blocked = True
        check("P", blocked and called["narrate"] == 0,
              "invalid deterministic report is blocked before narration")
    finally:
        orch.validate.validate = orig_validate
        orch.narrate_mod.narrate = orig_narrate

    # R/T: successful narration triggers second validation and renderer gets final report.
    validation_calls = {"n": 0}
    seen = {"report": None}
    orig_validate = orch.validate.validate
    orig_render = orch.render.render
    try:
        def counting_validate(*a, **k):
            validation_calls["n"] += 1
            return orig_validate(*a, **k)

        def capture_render(report, bundle=None):
            seen["report"] = report
            return orig_render(report, bundle)

        orch.validate.validate = counting_validate
        orch.render.render = capture_render

        def narrate_ok(system, user, **kw):
            return {"sections": []}

        run_ok = orch.run_assessment(
            state(),
            labor_realization=LaborRealization.COST_ELIMINATED,
            labor_realization_source=LaborRealizationSource.USER,
            enable_narration=True,
            narration_complete_json=narrate_ok,
        )
        check("R", validation_calls["n"] >= 2, "second validation occurs with narration enabled")
        check("T", seen["report"] == run_ok.final_report,
              "renderer receives the final validated report")
    finally:
        orch.validate.validate = orig_validate
        orch.render.render = orig_render

    # U no duplicate analytical execution.
    counts = {"est": 0, "rank": 0, "alt": 0, "sens": 0}
    orig_est = orch.estimator.estimate
    orig_rank = orch.driver_ranking.rank_drivers
    orig_alt = orch.alternatives_mod.derive
    orig_sens = orch.sensitivity_mod.sweep
    try:
        def c_est(*a, **k):
            counts["est"] += 1
            return orig_est(*a, **k)

        def c_rank(*a, **k):
            counts["rank"] += 1
            return orig_rank(*a, **k)

        def c_alt(*a, **k):
            counts["alt"] += 1
            return orig_alt(*a, **k)

        def c_sens(*a, **k):
            counts["sens"] += 1
            return orig_sens(*a, **k)

        orch.estimator.estimate = c_est
        orch.driver_ranking.rank_drivers = c_rank
        orch.alternatives_mod.derive = c_alt
        orch.sensitivity_mod.sweep = c_sens

        orch.run_assessment(
            state(),
            labor_realization=LaborRealization.COST_ELIMINATED,
            labor_realization_source=LaborRealizationSource.USER,
            enable_narration=False,
        )
        check("U", counts == {"est": 1, "rank": 1, "alt": 1, "sens": 1},
              f"no duplicate analytical stage execution ({counts})")
    finally:
        orch.estimator.estimate = orig_est
        orch.driver_ranking.rank_drivers = orig_rank
        orch.alternatives_mod.derive = orig_alt
        orch.sensitivity_mod.sweep = orig_sens


def case_W_X() -> None:
    print("\nW/X — conversation context reuse + compliance requirements propagate")
    install_llm_stub()
    ctx = ConversationContext()
    st = state()
    r1 = interviewer_engine.run_turn(st, "Hi, I'm Sam.", context=ctx)
    r2 = interviewer_engine.run_turn(st, "We process 20000 invoices monthly.", context=r1.context)
    check("W", r2.context is r1.context, "ConversationContext is carried, not recreated per turn")

    seen = {"ok": False}
    orig_est = orch.estimator.estimate
    try:
        def inspect(s):
            seen["ok"] = "HIPAA" in (s.risk.compliance_exposure or [])
            return solution()
        orch.estimator.estimate = inspect
        orch.run_assessment(
            state(risk=RiskInputs(failure_impact="wrong payment",
                                  compliance_exposure=["HIPAA"])),
            labor_realization=LaborRealization.COST_ELIMINATED,
            labor_realization_source=LaborRealizationSource.USER,
            enable_narration=False,
        )
        check("X", seen["ok"], "compliance requirements propagate through estimator path")
    finally:
        orch.estimator.estimate = orig_est


def case_AB() -> None:
    print("\nAB — refused reports reject forbidden key families")
    install_llm_stub()
    orig = orch.estimator.estimate
    try:
        def refuse(_state):
            return solution(recommended_pattern="", overall_automation=rng(0, 0))

        orch.estimator.estimate = refuse
        run = orch.run_assessment(
            state(),
            labor_realization=LaborRealization.COST_ELIMINATED,
            labor_realization_source=LaborRealizationSource.USER,
            enable_narration=False,
        )
        bad = run.final_report.model_copy(update={"sections": [
            s.model_copy(update={"figures": list(s.figures) + [
                f.model_copy(update={"key": "solution.pattern"}) for f in s.figures[:1]
            ]}) if s.key == "executive_summary" else s for s in run.final_report.sections
        ]})
        res = validate.validate(bad, run.bundle)
        check("AB", not res.valid and "refused_fabricated_value" in [e.code for e in res.errors],
              "refused validator blocks solution.* family keys")
    finally:
        orch.estimator.estimate = orig


def main() -> None:
    case_A_B_I_J_K_L_M_N_V()
    case_C_D_AA_AC()
    case_E_F_G_H_Y_Z_AD()
    case_O_P_Q_R_S_T_U()
    case_W_X()
    case_AB()

    print()
    if failures:
        print(f"{len(failures)} FAILURE(S):")
        for f in failures:
            print(f"  - {f}")
        sys.exit(1)
    print("ALL P6 ORCHESTRATION CASES PASSED")


if __name__ == "__main__":
    main()
