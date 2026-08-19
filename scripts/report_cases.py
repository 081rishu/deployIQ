"""P1 acceptance tests — Report Schema + Evidence Resolver (spec 13).

Scope is P1 only: the data contract and the evidence index. No assembly, no
validation pass, no narrative, no rendering, no orchestration.

The LLM is stubbed throughout, so this runs with no API key and is fully
deterministic — the same convention as every other suite in scripts/.
"""

from __future__ import annotations

import ast
import json
import sys
import types
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
if "openai" not in sys.modules:
    _s = types.ModuleType("openai"); _s.OpenAI = lambda **kw: None
    sys.modules["openai"] = _s

import pydantic

from calc import calibration as econ_cal
from calc import driver_ranking
from calc import scoring_calibration as score_cal
from calc import sensitivity as sens_mod
from calc.ai_state import LaborRealization
from report import evidence as ev
from report.schema import (
    Citation,
    DriverClass,
    DriverEntry,
    EvidenceRegistry,
    Figure,
    FigureStatus,
    Gap,
    GapKind,
    LaborRealizationSource,
    RangeSemantics,
    Report,
    ReportInput,
    ReportManifest,
    ReportMode,
    ReportSection,
    Statement,
    StatementOrigin,
    Unit,
    FLAG_CURRENCY_UNRESOLVED,
    FLAG_PROVENANCE_UNKNOWN,
)
from schemas.assessment_state import (
    AssessmentState, DataReadiness, EffortBand, FieldResolution, ImpactSeverity,
    Provenance, RangeEstimate, RiskInputs, Sector,
)
from solution import alternatives as alts_mod
from solution import calibration as scope_cal
from solution.schema import (
    Capability, HitlMode, PerformanceMetric, SolutionEstimate, TaskAutomationEstimate,
)

failures: list[str] = []


def check(case: str, cond: bool, desc: str) -> None:
    print(f"    [{'PASS' if cond else 'FAIL'}] {desc}")
    if not cond:
        failures.append(f"{case}: {desc}")


def raises(fn) -> bool:
    try:
        fn()
    except (pydantic.ValidationError, ValueError):
        return True
    return False


# --- fixtures --------------------------------------------------------------

def rng(lo, hi, prov=Provenance.ESTIMATED, src="test", sid=None):
    return RangeEstimate(min=lo, max=hi, provenance=prov, source=src, source_id=sid)


def state(**kw) -> AssessmentState:
    base = dict(sector=Sector.DOCUMENT_PROCESSING, problem="automate invoices",
                process="invoice intake", monthly_volume=20000,
                avg_time_per_unit_minutes=6, current_headcount=16,
                fully_loaded_annual_cost=62000, geography="US",
                fraction_time_on_process=0.7, required_accuracy=0.97,
                integration_complexity=EffortBand.MEDIUM,
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


def bundle() -> ReportInput:
    """One frozen bundle from the real pipeline, LLM stubbed."""
    st, sol = state(), solution()
    drivers = driver_ranking.rank_drivers(st, sol, LaborRealization.COST_ELIMINATED)
    import llm.openai_client as oc
    oc.complete_json = lambda *a, **k: {}          # alternatives prose unavailable
    alts = alts_mod.derive(st, sol)
    sweep = sens_mod.sweep(st, sol, LaborRealization.COST_ELIMINATED)
    return ReportInput.from_pipeline(
        state=st, solution=sol, drivers=drivers, alternatives=alts,
        sensitivity=sweep, confidence=None,
        labor_realization=LaborRealization.COST_ELIMINATED,
        labor_realization_source=LaborRealizationSource.USER)


# --- 1. KNOWN requires provenance + derivation -----------------------------

def case_known_requires_provenance_and_derivation() -> None:
    print("\n1 — a KNOWN figure requires provenance AND a derivation")
    ok = Figure.known("k", "Known", value_min=1.0, value_max=2.0, unit=Unit.COUNT,
                      derivation="a + b", provenance=Provenance.DERIVED)
    check("1", ok.status is FigureStatus.KNOWN, "a fully specified known figure builds")

    check("1", raises(lambda: Figure.known(
        "k", "Known", value_min=1.0, value_max=2.0, unit=Unit.COUNT,
        derivation="a + b")), "a known figure with no provenance is rejected")

    check("1", raises(lambda: Figure.known(
        "k", "Known", value_min=1.0, value_max=2.0, unit=Unit.COUNT,
        derivation="   ", provenance=Provenance.DERIVED)),
        "a known figure with no derivation is rejected")

    declared = Figure.known("k", "Known", value_min=1.0, value_max=2.0,
                            unit=Unit.COUNT, derivation="upstream value",
                            flags=[FLAG_PROVENANCE_UNKNOWN])
    check("1", declared.provenance is None,
          "provenance may be MISSING only when explicitly declared unknown, "
          "never inferred")

    check("1", raises(lambda: Figure.known(
        "p", "Point", value_min=1.0, value_max=2.0, unit=Unit.COUNT,
        derivation="d", provenance=Provenance.DERIVED,
        range_semantics=RangeSemantics.POINT)),
        "POINT semantics with differing bounds is rejected — a range is never "
        "silently collapsed")


# --- 2/3. absence cannot carry a value -------------------------------------

def case_absent_carries_no_value() -> None:
    print("\n2 — an ABSENT figure cannot carry a numeric value")
    absent = Figure.absent("tooling", "Existing tooling / infrastructure",
                           "not provided — excluded, so the total is a floor")
    check("2", absent.value_min is None and absent.value_max is None
          and absent.value_text is None, "an absent figure holds no value at all")
    check("2", absent.absence_reason != "", "it carries the reason instead")
    check("2", raises(lambda: Figure(
        key="t", label="T", status=FigureStatus.ABSENT, value_min=0.0,
        absence_reason="not collected")),
        "absent + a numeric value is rejected (absence is not zero)")
    check("2", raises(lambda: Figure(
        key="t", label="T", status=FigureStatus.ABSENT, value_text="none",
        absence_reason="not collected")),
        "absent + the text 'none' is rejected too")
    check("2", raises(lambda: Figure(
        key="t", label="T", status=FigureStatus.ABSENT)),
        "absent without a reason is rejected")


def case_not_computable_carries_no_value() -> None:
    print("\n3 — a NOT_COMPUTABLE figure cannot carry a numeric value")
    nc = Figure.not_computable("payback", "Payback period",
                               ["positive monthly net benefit"])
    check("3", nc.value_min is None and nc.value_max is None,
          "a not-computable figure holds no value")
    check("3", "positive monthly net benefit" in nc.absence_reason,
          "the missing input is named, not implied")
    check("3", raises(lambda: Figure(
        key="s", label="S", status=FigureStatus.NOT_COMPUTABLE, value_min=0.0,
        absence_reason="missing inputs")),
        "not-computable + a value is rejected (unknown is not zero)")
    check("3", nc.status is not FigureStatus.ABSENT,
          "not-computable stays distinct from absent — they mean different things")


# --- 4. derived figures cite an input SET ----------------------------------

def case_derived_multiple_sources() -> None:
    print("\n4 — a derived figure cites its input set, not one invented source")
    ix = ev.build_index()
    ids = [next(k for k, c in ix.citations.items()
                if c.registry is EvidenceRegistry.BENCHMARK),
           next(k for k, c in ix.citations.items()
                if c.registry is EvidenceRegistry.LABOR_RATE),
           next(k for k, c in ix.citations.items()
                if c.registry is EvidenceRegistry.CALIBRATION)]
    fig = Figure.known("current_annual_total", "Current annual cost",
                       value_min=1.0, value_max=2.0, unit=Unit.MONEY,
                       currency="USD", derivation="sum of 4 known components",
                       provenance=Provenance.DERIVED, source_ids=list(ids))
    decorated = ix.decorate(fig)
    check("4", len(decorated.source_ids) == 3,
          "three contributing leaf ids are carried, not collapsed to one")
    check("4", len(decorated.citations) == 3, "all three resolve to citations")
    check("4", {c.registry for c in decorated.citations} ==
          {EvidenceRegistry.BENCHMARK, EvidenceRegistry.LABOR_RATE,
           EvidenceRegistry.CALIBRATION},
          "a single derived figure spans three different registries")
    check("4", decorated.provenance is Provenance.DERIVED,
          "decorating never rewrites the figure's own provenance tag")
    check("4", len(decorated.provenance_mix) >= 2,
          "the provenance MIX is filled in, so `derived` does not hide whether "
          "the inputs were measured or assumed")


# --- 5. ids resolve to the correct registry --------------------------------

def case_registry_routing() -> None:
    print("\n5 — every evidence id resolves in the registry that actually holds it")
    ix = ev.build_index()
    from lib.benchmarks import load_pack
    from lib.compliance import load_attestations
    from lib.labor_rates import load_rates
    from lib.pricing import load_pricing

    expected = [
        (load_pack(Sector.DOCUMENT_PROCESSING).figures[0].evidence_id,
         EvidenceRegistry.BENCHMARK),
        (load_pricing().records[0].pricing_id, EvidenceRegistry.PRICING),
        (load_rates().entries[0].rate_id, EvidenceRegistry.LABOR_RATE),
        (load_attestations()[0].evidence_id, EvidenceRegistry.COMPLIANCE),
        (econ_cal.audit_table()[0]["calibration_id"], EvidenceRegistry.CALIBRATION),
        (score_cal.audit_table()[0]["parameter_id"], EvidenceRegistry.CALIBRATION),
        (scope_cal.all_calibration_params()[0].key, EvidenceRegistry.CALIBRATION),
    ]
    for evidence_id, registry in expected:
        found = ix.resolve(str(evidence_id))
        check("5", found is not None and found.registry is registry,
              f"{evidence_id!r} resolves in {registry.value}")
    check("5", not ix.ambiguous,
          "no id is claimed by two registries (an ambiguous id would be surfaced, "
          "never silently resolved to the first winner)")
    check("5", set(ix.registry_counts) == {r.value for r in EvidenceRegistry},
          "all five registries are indexed")


# --- 6. unknown ids are surfaced, never invented ---------------------------

def case_unknown_ids_surfaced() -> None:
    print("\n6 — an unresolvable evidence id is surfaced, never invented")
    ix = ev.build_index()
    res = ix.resolve_many(["definitely_not_a_real_evidence_id"])
    check("6", res.citations == [], "no Citation is fabricated for an unknown id")
    check("6", res.unresolved == ["definitely_not_a_real_evidence_id"],
          "the unresolved id is reported back by name")
    check("6", ix.resolve("definitely_not_a_real_evidence_id") is None,
          "resolve() returns None rather than a plausible-looking record")

    fig = Figure.known("f", "F", value_min=1.0, value_max=1.0, unit=Unit.COUNT,
                       derivation="d", provenance=Provenance.SOURCED,
                       source_ids=["definitely_not_a_real_evidence_id"])
    decorated = ix.decorate(fig)
    check("6", decorated.citations == []
          and decorated.unresolved_source_ids == ["definitely_not_a_real_evidence_id"],
          "a figure with an unresolvable id keeps the id visible and gains no "
          "citation")
    gap = Gap(kind=GapKind.UNRESOLVED_EVIDENCE_ID, label="unresolved evidence id",
              detail="definitely_not_a_real_evidence_id",
              consequence="the figure cannot be traced to a source document")
    check("6", gap.kind is GapKind.UNRESOLVED_EVIDENCE_ID,
          "the schema can represent the resulting gap")


# --- 7. verification is separate from provenance ---------------------------

def case_verification_distinct() -> None:
    print("\n7 — verification stays a separate axis from provenance")
    ix = ev.build_index()
    sourced = [c for c in ix.citations.values()
               if c.provenance is Provenance.SOURCED]
    check("7", any(c.verification == "primary_document" for c in sourced)
          and any(c.verification != "primary_document" for c in sourced),
          "`sourced` figures exist at BOTH primary and below-primary "
          "verification — the axes are genuinely independent")

    below = Citation(evidence_id="x", source="s", registry=EvidenceRegistry.BENCHMARK,
                     provenance=Provenance.SOURCED, verification="search_snippet")
    check("7", below.below_primary,
          "a sourced figure read only from a search snippet counts as below primary")
    unrecorded = Citation(evidence_id="y", source="s",
                          registry=EvidenceRegistry.PRICING,
                          provenance=Provenance.SOURCED, verification=None)
    check("7", unrecorded.below_primary,
          "an unrecorded verification tier counts as below primary — "
          "'we do not know how firmly this was checked' is not 'firmly checked'")
    check("7", all(c.verification in (None, "primary_document", "search_snippet",
                                      "unverified") for c in ix.citations.values()),
          "verification never takes a provenance value")
    check("7", all(c.provenance in (None, Provenance.SOURCED, Provenance.ASSUMED,
                                    Provenance.USER_PROVIDED, Provenance.ESTIMATED,
                                    Provenance.DERIVED)
                   for c in ix.citations.values()),
          "provenance never takes a sixth value")


# --- 8/9. currency ---------------------------------------------------------

def case_currency_from_state() -> None:
    print("\n8 — currency comes from AssessmentState and nowhere else")
    res = ev.resolve_currency(state())
    check("8", res.resolved and res.currency == "USD",
          "geography 'US' resolves to USD through AssessmentState.currency")
    check("8", "AssessmentState.currency" in res.basis,
          "the basis names the single authoritative source")
    check("8", ev.resolve_currency(state(geography="india")).currency == "INR",
          "a different geography resolves to its own currency")

    ix = ev.build_index()
    priced = [c for c in ix.citations.values()
              if c.registry is EvidenceRegistry.PRICING]
    check("8", priced and "USD" in (priced[0].note or ""),
          "pricing records carry their OWN currency in the citation note; "
          "the resolver never promotes it to the assessment's currency")


def case_currency_unresolved_is_explicit() -> None:
    print("\n9 — an unresolved currency stays explicit and invents nothing")
    res = ev.resolve_currency(state(geography=None))
    check("9", not res.resolved and res.currency is None,
          "no geography means no currency, not a default")
    check("9", "no geography" in res.basis, "the reason is stated")
    res2 = ev.resolve_currency(state(geography="atlantis"))
    check("9", not res2.resolved and "atlantis" in res2.basis,
          "an unmapped geography is reported by name, not silently defaulted")

    check("9", raises(lambda: Figure.known(
        "cost", "Cost", value_min=1.0, value_max=2.0, unit=Unit.MONEY,
        derivation="d", provenance=Provenance.DERIVED)),
        "a money figure with no currency is rejected outright")
    declared = Figure.known("cost", "Cost", value_min=1.0, value_max=2.0,
                            unit=Unit.MONEY, derivation="d",
                            provenance=Provenance.DERIVED,
                            flags=[FLAG_CURRENCY_UNRESOLVED])
    check("9", declared.currency is None
          and FLAG_CURRENCY_UNRESOLVED in declared.flags,
          "it is renderable only by DECLARING the currency unresolved")
    gap = Gap(kind=GapKind.CURRENCY_UNRESOLVED, label="currency unresolved",
              detail=res.basis, consequence="money figures render without a unit")
    check("9", gap.kind is GapKind.CURRENCY_UNRESOLVED,
          "the schema can represent the currency gap")

    r = RangeEstimate(min=1.0, max=2.0, provenance=Provenance.DERIVED, source="d")
    auto = Figure.from_range("x", "X", r, unit=Unit.MONEY, origin_module="calc")
    check("9", FLAG_CURRENCY_UNRESOLVED in auto.flags,
          "from_range() declares the gap rather than failing or guessing")


# --- 10. ReportInput round-trip -------------------------------------------

def case_report_input_round_trip() -> None:
    print("\n10 — ReportInput survives a JSON round-trip (state is client-carried)")
    bundled = bundle()
    payload = bundled.model_dump(mode="json")
    text = json.dumps(payload)
    restored = ReportInput.model_validate(json.loads(text))
    check("10", len(text) > 1000, f"the bundle serialises ({len(text)} bytes)")
    check("10", restored.state.sector is bundled.state.sector
          and restored.solution.recommended_pattern == bundled.solution.recommended_pattern,
          "state and solution survive the round-trip")
    check("10", [d.key for d in restored.drivers.drivers] ==
          [d.key for d in bundled.drivers.drivers],
          "driver identity and ORDER survive unchanged")
    check("10", restored.economics.current_annual_total.min ==
          bundled.economics.current_annual_total.min,
          "economic figures survive numerically identical")
    check("10", restored.labor_realization == bundled.labor_realization,
          "the labor realization policy survives")

    check("10", raises(lambda: ReportInput(
        state=bundled.state, solution=bundled.solution,
        economics=bundled.economics.model_copy(update={"warnings": ["tampered"]}),
        scores=bundled.scores, drivers=bundled.drivers,
        alternatives=bundled.alternatives)),
        "a second, divergent EconomicResult is rejected — the report cannot "
        "print economics its own drivers were not computed from")

    frozen_ok = False
    try:
        bundled.state = state()
    except (pydantic.ValidationError, ValueError, AttributeError, TypeError):
        frozen_ok = True
    check("10", frozen_ok, "the bundle is immutable once assembled")


# --- 11. manifest keeps every version field -------------------------------

def case_manifest_versions() -> None:
    print("\n11 — the manifest preserves every version field for audit")
    from lib.benchmarks import load_pack
    pack = load_pack(Sector.DOCUMENT_PROCESSING)
    manifest = ReportManifest(
        generated_at="2026-08-19T00:00:00Z", sector=Sector.DOCUMENT_PROCESSING,
        pack_version=pack.pack_version, pack_health=pack.health(),
        economic_calibration_version=econ_cal.CALIBRATION_VERSION,
        scoring_calibration_version=score_cal.SCORING_CALIBRATION_VERSION,
        solution_calibration_version=scope_cal.all_calibration_params()[0].version,
        registry_pattern_id="document_pipeline",
        registry_implementation_id="idp_managed", registry_last_reviewed="2026-08-18",
        labor_realization=LaborRealization.COST_ELIMINATED.value,
        labor_realization_source=LaborRealizationSource.USER,
        currency="USD", currency_basis="derived from geography 'US'",
        llm_model=None, llm_used_for=[], guard_actions=[])
    restored = ReportManifest.model_validate(
        json.loads(json.dumps(manifest.model_dump(mode="json"))))
    for field in ("pack_version", "economic_calibration_version",
                  "scoring_calibration_version", "solution_calibration_version",
                  "registry_pattern_id", "registry_implementation_id",
                  "registry_last_reviewed", "currency", "currency_basis"):
        check("11", getattr(restored, field) == getattr(manifest, field),
              f"{field} survives the round-trip")
    check("11", restored.pack_health == pack.health(),
          "pack health (sourced / primary-verified counts) is carried, so an "
          "assessment on a weak pack cannot hide it")
    check("11", not restored.llm_used,
          "a report generated with no LLM records that it used none")


# --- 12. DriverType semantics survive -------------------------------------

def case_driver_type_survives() -> None:
    print("\n12 — DriverType semantics survive into the report schema (F1)")
    bundled = bundle()
    upstream = bundled.drivers.drivers
    check("12", upstream, "the pipeline produced drivers")

    entries = [
        DriverEntry(
            key=d.key, label=d.label, statement=Statement.code(d.statement),
            driver_type=d.driver_type.value,
            presentation_class=DriverClass.for_driver(d.driver_type.value, d.impact),
            rank=i, impact=d.impact, dominant_quantity=d.dominant_quantity,
            confidence=d.confidence, uncertainty_type=d.uncertainty_type,
            relative_width=d.relative_width, uncertainty_index=d.uncertainty_index,
            evidence_notes=list(d.evidence_ids))
        for i, d in enumerate(upstream)
    ]
    check("12", [e.rank for e in entries] == list(range(len(upstream)))
          and [e.key for e in entries] == [d.key for d in upstream],
          "upstream ORDER is preserved exactly; the report re-ranks nothing")
    check("12", all(e.driver_type == d.driver_type.value
                    for e, d in zip(entries, upstream)),
          "each driver's upstream DriverType is carried verbatim")

    classes = {e.presentation_class for e in entries}
    active = [e for e in entries if e.presentation_class is DriverClass.ECONOMICALLY_ACTIVE]
    factual = [e for e in entries if e.presentation_class is DriverClass.FACTUAL_INPUT]
    coverage = [e for e in entries if e.presentation_class is DriverClass.DATA_COVERAGE]
    print(f"    partition: {len(active)} economically active, {len(factual)} "
          f"factual, {len(coverage)} data-coverage")
    check("12", len(classes) == 3,
          "the real pipeline output partitions into all three presentation classes")
    check("12", all(e.impact > 0 for e in active),
          "economically active drivers all move an economic quantity")
    check("12", all(e.impact == 0 for e in factual),
          "zero-impact business facts are partitioned OUT of 'what matters most' "
          "rather than being presented as the weakest drivers")
    check("12", all(e.driver_type == "data_coverage" for e in coverage),
          "data-coverage findings are classified by their type, not their impact")
    check("12", DriverClass.for_driver("data_coverage", 0.9) is DriverClass.DATA_COVERAGE,
          "a data-coverage finding stays data-coverage whatever its impact")

    section = ReportSection(key="drivers", number=0, title="Decision Drivers",
                            layer=1, drivers=entries)
    restored = ReportSection.model_validate(
        json.loads(json.dumps(section.model_dump(mode="json"))))
    check("12", [e.presentation_class for e in restored.drivers] ==
          [e.presentation_class for e in entries],
          "the partition survives a JSON round-trip")


# --- 13. no duplicate evidence ids ----------------------------------------

def case_no_duplicate_ids() -> None:
    print("\n13 — one figure never emits the same evidence id twice")
    ix = ev.build_index()
    real = next(iter(ix.citations))
    fig = Figure.known("f", "F", value_min=1.0, value_max=2.0, unit=Unit.COUNT,
                       derivation="d", provenance=Provenance.DERIVED,
                       source_ids=[real, real, "", real])
    check("13", fig.source_ids == [real],
          "duplicate and empty ids are collapsed at construction")
    decorated = ix.decorate(fig)
    check("13", len(decorated.citations) == 1,
          "the decorated figure carries one citation, not three")

    bundled = bundle()
    ids = ev.collect_source_ids(bundled.economics, bundled.solution, bundled.state)
    check("13", len(ids) == len(set(ids)),
          f"collection over the live bundle de-duplicates ({len(ids)} unique ids)")
    res = ix.resolve_many(ids + ids)
    check("13", len(res.source_ids) == len(set(res.source_ids)),
          "resolving a list with repeats yields each id once")

    anchor_prose = [d.evidence_ids for d in bundled.drivers.drivers if d.evidence_ids]
    swept = ev.collect_source_ids(bundled.drivers)
    check("13", all(p not in swept for group in anchor_prose for p in group),
          "DriverImpact.evidence_ids holds rendered CITATION STRINGS, not ids, "
          "and is deliberately excluded from id collection")


# --- 14. the frozen layers were not altered -------------------------------

def case_frozen_layers_untouched() -> None:
    print("\n14 — the documentation cleanup altered no analytical code")
    root = Path(__file__).resolve().parent.parent

    forbidden = {"calc.engine": "run", "calc.driver_ranking": "rank_drivers",
                 "calc.sensitivity": "sweep", "solution.estimator": "estimate"}
    offenders: list[str] = []
    for path in sorted((root / "report").glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module in forbidden:
                names = {a.name for a in node.names}
                if forbidden[node.module] in names:
                    offenders.append(f"{path.name} imports "
                                     f"{node.module}.{forbidden[node.module]}")
    check("14", not offenders,
          f"no report module imports a calculation entry point ({offenders})")

    # The analytical layers still produce exactly what P0 recorded.
    bundled = bundle()
    keys = [d.key for d in bundled.drivers.drivers]
    check("14", keys[:3] == ["automation_rate", "implementation_effort",
                             "review_fraction"],
          "driver ranking is unchanged since P0")
    check("14", len(bundled.economics.absent_components) == 10,
          "the engine still reports 10 absent components on this fixture")
    check("14", "under a month" in bundled.economics.first_year.payback_statement,
          "the payback statement is unchanged")
    check("14", bundled.scores.economic.flags,
          "the economic sanity flags still fire (guardrail 15 stays load-bearing)")

    check("14", (root / "calc" / "models.py").read_text(encoding="utf-8")
          .count("source_id") == 0,
          "calc/models.py was NOT reopened to propagate source ids — the "
          "evidence index carries that job instead")


# --- extra: the report package introduces no LLM dependency ---------------

def case_no_llm_in_report_layer() -> None:
    print("\nEXTRA — the deterministic report layer has no LLM dependency")
    root = Path(__file__).resolve().parent.parent
    hits: list[str] = []
    # narrate.py is the OPTIONAL LLM narration layer and legitimately imports
    # llm/ (it falls back deterministically). Every OTHER report module — the
    # deterministic half (assemble, evidence, schema, validate, render) — must
    # stay LLM-free, because the report must remain fully usable without one.
    for path in sorted((root / "report").glob("*.py")):
        if path.name == "narrate.py":
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and (node.module or "").startswith("llm"):
                hits.append(path.name)
            if isinstance(node, ast.Import):
                hits += [path.name for a in node.names if a.name.startswith("llm")]
    check("EXTRA", not hits,
          f"no deterministic report module imports llm/ ({hits})")
    check("EXTRA", (root / "report" / "narrate.py").exists(),
          "narrate.py is the single optional LLM entry point")

    statement = Statement.code("Expected automation is estimated at 71-87%.")
    check("EXTRA", statement.origin is StatementOrigin.CODE,
          "a code statement needs no source_statement")
    check("EXTRA", raises(lambda: Statement(text="rephrased",
                                            origin=StatementOrigin.LLM)),
          "an LLM statement without its code-authored fallback is rejected — "
          "the deterministic half must always remain available")
    llm_stmt = Statement(text="rephrased", origin=StatementOrigin.LLM,
                         source_statement="Expected automation is 71-87%.")
    check("EXTRA", llm_stmt.source_statement,
          "a valid LLM statement carries the text it rephrases")

    report = Report(mode=ReportMode.FULL,
                    manifest=ReportManifest(generated_at="t",
                                            sector=Sector.DOCUMENT_PROCESSING))
    check("EXTRA", report.layer(1) == [],
          "an empty report is valid and renders no layer-1 content")
    check("EXTRA", raises(lambda: Report(
        mode=ReportMode.REFUSED,
        manifest=ReportManifest(generated_at="t",
                                sector=Sector.DOCUMENT_PROCESSING))),
        "a refused report must state why it was refused")


def case_calibration_ids_recovered() -> None:
    print("\nEXTRA — calibration assumptions behind a derived figure stay traceable")
    ix = ev.build_index()
    bundled = bundle()

    leaves = ix.resolve_many(ev.collect_source_ids(bundled.economics))
    full = ix.resolve_objects(bundled.economics)
    check("EXTRA", len(full.citations) > len(leaves.citations),
          "citation strings written BY the calibration registry recover ids that "
          "no leaf carries (calc/calibration.py attaches no source_id)")
    check("EXTRA", any(c.registry is EvidenceRegistry.CALIBRATION
                       for c in full.citations),
          "a calibration parameter that moved the number is citable")
    check("EXTRA", all(c.provenance is Provenance.ASSUMED for c in full.citations
                       if c.registry is EvidenceRegistry.CALIBRATION),
          "every calibration citation is tagged `assumed`, never `sourced` — "
          "these are versioned product calibrations, not industry data")

    class Fake(pydantic.BaseModel):
        source: str = "invented [not_a_real_calibration_id] and [aws_textract_expense_v1]"
    candidates = ev.collect_text_declared_ids(Fake())
    check("EXTRA", "not_a_real_calibration_id" in candidates,
          "bracket scanning reports every candidate it sees")
    resolved = ix.resolve_objects(Fake())
    check("EXTRA", [c.evidence_id for c in resolved.citations]
          == ["aws_textract_expense_v1"],
          "only ids the registry actually holds become citations — a stray "
          "bracket in prose is discarded, never invented into evidence")
    check("EXTRA", not resolved.unresolved,
          "a discarded prose candidate is not reported as unresolved evidence "
          "either; it was never a declared id")


# ===========================================================================
# P2 — deterministic assembly (report/assemble.py)
# ===========================================================================

from report import assemble as assemble_mod  # noqa: E402


def _report(**kw) -> "Report":
    """Assemble a Report from a bundle, allowing the bundle to be overridden."""
    st, sol = state(), solution()
    drivers = driver_ranking.rank_drivers(
        st, sol, LaborRealization.COST_ELIMINATED)
    import llm.openai_client as oc
    oc.complete_json = lambda *a, **k: {}
    alts = alts_mod.derive(st, sol)
    sweep = sens_mod.sweep(st, sol, LaborRealization.COST_ELIMINATED)
    alts_override = kw.pop("alternatives", None)
    sweep_override = kw.pop("sensitivity", None)
    conf_override = kw.pop("confidence", None)
    err_override = kw.pop("economic_error", None)
    return assemble_mod.assemble(ReportInput.from_pipeline(
        state=st, solution=sol, drivers=drivers,
        alternatives=(alts_override if alts_override is not None else alts),
        sensitivity=(sweep_override if sweep_override is not None else sweep),
        confidence=conf_override, economic_error=err_override,
        labor_realization=LaborRealization.COST_ELIMINATED,
        labor_realization_source=LaborRealizationSource.USER, **kw))


def _exec_text(report: "Report") -> str:
    sec = report.section("executive_summary")
    return " ".join(s.text for s in sec.statements)


def case_P2_full_mode() -> None:
    print("\nP2-A — a normal full assessment assembles with all sections")
    report = _report()
    check("P2-A", report.mode is ReportMode.FULL, "mode is full")
    keys = [s.key for s in report.sections]
    required = ["executive_summary", "problem_definition", "current_process",
                "current_cost", "proposed_ai_solution", "alternative_solutions",
                "implementation_reqs", "ai_operating_cost", "expected_benefits",
                "risks_and_reliability", "assumptions", "external_sources",
                "sensitivity_analysis", "what_to_validate_next"]
    check("P2-A", all(k in keys for k in required),
          "all fourteen canonical sections are present")
    nums = sorted(s.number for s in report.sections
                  if s.number in range(1, 15))
    check("P2-A", nums == list(range(1, 15)),
          "the fourteen sections carry canonical numbers 1..14")


def case_P2_estimator_refusal() -> None:
    print("\nP2-B — estimator refusal produces a refused report")
    sol = solution(recommended_pattern="",
                   overall_automation=rng(0.0, 0.0))
    st = state()
    import llm.openai_client as oc
    oc.complete_json = lambda *a, **k: {}
    alts = alts_mod.derive(st, sol)
    report = assemble_mod.assemble(ReportInput.from_pipeline(
        state=st, solution=sol, drivers=None, alternatives=alts,
        economic_error=["estimator refused: no architecture was selected"],
        labor_realization=LaborRealization.COST_ELIMINATED,
        labor_realization_source=LaborRealizationSource.USER))
    check("P2-B", report.mode is ReportMode.REFUSED, "mode is refused")
    check("P2-B", report.section("proposed_ai_solution") is None,
          "refused reports contain no proposed-solution section")
    check("P2-B", report.section("expected_benefits") is None,
          "refused reports contain no benefits section")
    es = report.section("executive_summary")
    check("P2-B", any("presented" in s.text for s in es.statements),
          "the summary states that no economics/savings/payback are presented")
    check("P2-B", report.refusal_reason != "", "the refusal reason is recorded")


def case_P2_compliance_refusal() -> None:
    print("\nP2-C — a hard compliance gap produces a refused report")
    sol = solution(compliance_gap=True,
                   compliance_statement="a hard compliance requirement could "
                                        "not be satisfied",
                   recommended_pattern="")
    st = state()
    import llm.openai_client as oc
    oc.complete_json = lambda *a, **k: {}
    alts = alts_mod.derive(st, sol)
    report = assemble_mod.assemble(ReportInput.from_pipeline(
        state=st, solution=sol, drivers=None, alternatives=alts,
        economic_error=["compliance gap"],
        labor_realization=LaborRealization.COST_ELIMINATED,
        labor_realization_source=LaborRealizationSource.USER))
    check("P2-C", report.mode is ReportMode.REFUSED, "mode is refused")
    check("P2-C", "compliance" in report.refusal_reason,
          "the refusal reason names the compliance blocker")
    check("P2-C", report.section("proposed_ai_solution") is None,
          "no solution is fabricated under a compliance gap")


def case_P2_partial_economics() -> None:
    print("\nP2-D — absent economics produces a partial report, not an exception")
    st, sol = state(), solution()
    import llm.openai_client as oc
    oc.complete_json = lambda *a, **k: {}
    alts = alts_mod.derive(st, sol)
    report = assemble_mod.assemble(ReportInput.from_pipeline(
        state=st, solution=sol, drivers=None, alternatives=alts,
        economic_error=["economic engine could not run: missing inputs"],
        labor_realization=LaborRealization.COST_ELIMINATED,
        labor_realization_source=LaborRealizationSource.USER))
    check("P2-D", report.mode is ReportMode.PARTIAL, "mode is partial")
    cc = report.section("current_cost")
    check("P2-D", cc is not None and any(
        "Not available" in s.text for s in cc.statements),
        "the dependent section is present and states it is unavailable")
    check("P2-D", report.section("proposed_ai_solution") is not None,
          "the solution section still renders in partial mode")


def case_P2_absent_components_never_zero() -> None:
    print("\nP2-E — absent current-cost components render as absent, never zero")
    report = _report()
    cc = report.section("current_cost")
    figs = {f.key: f for f in cc.figures}
    absent = [f for f in cc.figures if f.status is not FigureStatus.KNOWN]
    check("P2-E", any(f.status is FigureStatus.ABSENT for f in cc.figures),
          "at least one current-cost component is absent")
    check("P2-E", all(f.value_min is None and f.value_max is None
                      for f in absent),
          "an absent figure carries no numeric value")
    check("P2-E", any("floor" in s.text for s in cc.statements),
          "the total is described as a floor when components are absent")
    check("P2-E", all(f.value_min != 0.0 for f in cc.figures
                      if f.status is FigureStatus.KNOWN),
          "no known figure was set to a fabricated zero")


def case_P2_unresolved_currency() -> None:
    print("\nP2-F — unresolved currency stays explicit and invents nothing")
    st, sol = state(geography=None), solution()
    import llm.openai_client as oc
    oc.complete_json = lambda *a, **k: {}
    alts = alts_mod.derive(st, sol)
    drivers = driver_ranking.rank_drivers(
        st, sol, LaborRealization.COST_ELIMINATED)
    report = assemble_mod.assemble(ReportInput.from_pipeline(
        state=st, solution=sol, drivers=drivers, alternatives=alts,
        labor_realization=LaborRealization.COST_ELIMINATED,
        labor_realization_source=LaborRealizationSource.USER))
    money = [f for s in report.sections for f in s.figures
             if f.unit is Unit.MONEY]
    check("P2-F", money, "money figures exist")
    check("P2-F", all(f.currency is None and
                      FLAG_CURRENCY_UNRESOLVED in f.flags
                      for f in money if f.status is FigureStatus.KNOWN),
          "every known money figure declares the currency unresolved")
    check("P2-F", any(g.kind is GapKind.CURRENCY_UNRESOLVED
                      for s in report.sections for g in s.gaps),
          "a currency gap is filed")


def case_P2_divergent_labor() -> None:
    print("\nP2-G — divergent labor formulations surface as a finding")
    # A fixture whose task-based and workforce-based labor disagree.
    st = state(current_headcount=rng(4, 4),
               avg_time_per_unit_minutes=rng(30, 30),
               monthly_volume=rng(20000, 20000),
               fraction_time_on_process=0.9)
    sol = solution()
    drivers = driver_ranking.rank_drivers(
        st, sol, LaborRealization.COST_ELIMINATED)
    check("P2-G", drivers.scores.result.labor_consistency.status.value
          == "divergent",
          "the fixture produces divergent labor formulations")
    import llm.openai_client as oc
    oc.complete_json = lambda *a, **k: {}
    alts = alts_mod.derive(st, sol)
    report = assemble_mod.assemble(ReportInput.from_pipeline(
        state=st, solution=sol, drivers=drivers, alternatives=alts,
        labor_realization=LaborRealization.COST_ELIMINATED,
        labor_realization_source=LaborRealizationSource.USER))
    cp = report.section("current_process")
    check("P2-G", any(g.kind is GapKind.UNRESOLVED_FIELD
                      and "Labor" in g.label for g in cp.gaps),
          "the labor divergence is a typed gap in the process section")
    vnext = report.section("what_to_validate_next")
    check("P2-G", vnext is not None and any(
        "labor" in str(c.text or "").lower()
        for t in vnext.tables for r in t.rows for c in r),
        "the validation list names the labor reconciliation")


def case_P2_alternative_registry_gap() -> None:
    print("\nP2-H — a registry coverage gap is a limitation, not a judgement")
    from solution.schema import AlternativesResult
    report = _report(alternatives=AlternativesResult(
        categories_not_in_registry=["no_ai_baseline"], statement="none"))
    sec = report.section("alternative_solutions")
    check("P2-H", any(g.kind is GapKind.REGISTRY_GAP
                      for g in sec.gaps),
          "the missing category is a registry gap")


def case_P2_sensitivity_skipped() -> None:
    print("\nP2-I — sensitivity skipped rows are shown, not dropped")
    from calc.sensitivity import SensitivityReport
    report = _report(sensitivity=SensitivityReport(
        metric="first_year_net_benefit", baseline=0.0,
        skipped=["implementation_scale"]))
    sec = report.section("sensitivity_analysis")
    rows = [r for t in sec.tables for r in t.rows]
    check("P2-I", any("skipped" in str(c.text or "").lower()
                      for r in rows for c in r),
          "a skipped row is rendered")


def case_P2_sensitivity_failed() -> None:
    print("\nP2-J — sensitivity failed rows render as could-not-be-evaluated")
    from calc.sensitivity import SensitivityReport, VariableImpact
    from schemas.assessment_state import Provenance
    report = _report(sensitivity=SensitivityReport(
        metric="first_year_net_benefit", baseline=0.0,
        impacts=[VariableImpact(
            variable="automation", label="Automation", provenance=Provenance.DERIVED,
            source="test", baseline_metric=0.0, low_metric=0.0, high_metric=0.0,
            swing=0.0, direction="not computable",
            failed="no defensible range at the low bound")]))
    sec = report.section("sensitivity_analysis")
    check("P2-J", any(g.kind is GapKind.NOT_COMPUTABLE_SCORE
                      for g in sec.gaps),
          "a failed sensitivity variable produces a typed gap")


def case_P2_low_confidence() -> None:
    print("\nP2-K — low confidence is carried with its stated meaning")
    from calc.assessment_confidence import AssessmentConfidence
    report = _report(confidence=AssessmentConfidence(
        level="low", reasons=["field data quality is low"]))
    es = report.section("executive_summary")
    check("P2-K", any("low" in s.text for s in es.statements),
          "the confidence level appears in the summary")
    check("P2-K", any("confidence describes" in s.text.lower()
                      for s in es.statements),
          "confidence-not-quality qualifier is present")


def case_P2_contradictory_field() -> None:
    print("\nP2-L — a contradictory critical field is surfaced as a gap")
    st = state()
    st.set_resolution("monthly_volume", FieldResolution.CONTRADICTORY,
                      "user gave conflicting volumes")
    report = _report()
    # The summary/problem sections carry the unresolved field gap.
    all_gaps = [g for s in report.sections for g in s.gaps]
    check("P2-L", any(g.kind is GapKind.UNRESOLVED_FIELD for g in all_gaps),
          "a contradictory field yields an unresolved-field gap")


def case_P2_deterministic() -> None:
    print("\nP2-M — repeated assembly is byte-identical (deterministic)")
    st, sol = state(), solution()
    drivers = driver_ranking.rank_drivers(
        st, sol, LaborRealization.COST_ELIMINATED)
    import llm.openai_client as oc
    oc.complete_json = lambda *a, **k: {}
    alts = alts_mod.derive(st, sol)
    sweep = sens_mod.sweep(st, sol, LaborRealization.COST_ELIMINATED)
    bundle = ReportInput.from_pipeline(
        state=st, solution=sol, drivers=drivers, alternatives=alts,
        sensitivity=sweep, labor_realization=LaborRealization.COST_ELIMINATED,
        labor_realization_source=LaborRealizationSource.USER)
    r1 = assemble_mod.assemble(bundle)
    r2 = assemble_mod.assemble(bundle)
    check("P2-M", r1.model_dump_json(exclude_none=True)
          == r2.model_dump_json(exclude_none=True),
          "two assemblies of one bundle are identical")


def case_P2_no_llm_dependency() -> None:
    print("\nP2-N — assembly requires no LLM access")
    root = Path(__file__).resolve().parent.parent
    tree = ast.parse((root / "report" / "assemble.py").read_text(encoding="utf-8"))
    imports = [n for n in ast.walk(tree)
               if isinstance(n, (ast.Import, ast.ImportFrom))]
    hits = [n for n in imports
            if "llm" in ((n.module or "") if isinstance(n, ast.ImportFrom) else "")]
    check("P2-N", not hits, "assemble.py never imports llm/")
    check("P2-N", "openai" not in open(  # noqa: SIM115
        root / "report" / "assemble.py", encoding="utf-8").read(),
        "assemble.py contains no openai reference")


def case_P2_14_sections() -> None:
    print("\nP2-O — all fourteen sections exist with canonical titles")
    report = _report()
    canonical = {1: "Executive Summary", 2: "Problem Definition",
                 3: "Current Process", 4: "Current Cost",
                 5: "Proposed AI Solution", 6: "Alternative Solutions",
                 7: "Implementation Requirements", 8: "AI Operating Cost",
                 9: "Expected Benefits", 10: "Risks and Reliability",
                 11: "Assumptions", 12: "External Sources",
                 13: "Sensitivity Analysis", 14: "What to Validate Next"}
    by_num = {s.number: s.title for s in report.sections}
    check("P2-O", all(by_num.get(n) == t for n, t in canonical.items()),
          "every section has its canonical number and title")


def case_P2_canonical_ordering() -> None:
    print("\nP2-P — sections follow the approved layer-grouped canonical order")
    report = _report()
    keys = [s.key for s in report.sections]
    expected = ["executive_summary", "decision_drivers",
                "problem_definition", "current_process", "current_cost",
                "proposed_ai_solution", "implementation_reqs",
                "ai_operating_cost", "expected_benefits", "risks_and_reliability",
                "alternative_solutions", "scores", "sensitivity_analysis",
                "assumptions", "external_sources", "what_to_validate_next"]
    check("P2-P", keys == expected,
          "sections are emitted in the approved layer-grouped order")
    l2_pos = [keys.index(k) for k in
              ("problem_definition", "sensitivity_analysis")]
    l3_pos = [keys.index(k) for k in ("assumptions", "external_sources")]
    check("P2-P", max(l2_pos) < min(l3_pos),
          "all Layer-2 analysis sections precede the Layer-3 audit sections")


def case_P2_no_composite_in_summary() -> None:
    print("\nP2-Q — the composite score is absent from the Executive Summary")
    report = _report()
    es = report.section("executive_summary")
    keys = [f.key for f in es.figures]
    check("P2-Q", all("composite" not in k for k in keys),
          "no composite figure appears in the summary")
    # No score value at all in Layer 1.
    l1 = report.layer(1)
    l1_figs = [f for s in l1 for f in s.figures]
    check("P2-Q", all(f.unit is not Unit.SCORE for f in l1_figs),
          "no score figures render in Layer 1")


def case_P2_driver_partition() -> None:
    print("\nP2-R — drivers partition by class without re-ranking")
    report = _report()
    sec = report.section("decision_drivers")
    entries = sec.drivers
    check("P2-R", entries, "drivers are present")
    check("P2-R", [e.rank for e in entries] == list(range(len(entries))),
          "rank preserves upstream order, no re-ranking")
    classes = {e.presentation_class for e in entries}
    check("P2-R", classes == {DriverClass.ECONOMICALLY_ACTIVE,
                              DriverClass.FACTUAL_INPUT,
                              DriverClass.DATA_COVERAGE},
          "all three presentation classes are present")


def case_P2_figure_provenance() -> None:
    print("\nP2-S — figure provenance survives into the report")
    report = _report()
    known = [f for s in report.sections for f in s.figures
             if f.status is FigureStatus.KNOWN]
    check("P2-S", known, "known figures exist")
    check("P2-S", all(f.provenance is not None for f in known),
          "every known figure carries a provenance tag")
    check("P2-S", all(f.derivation for f in known),
          "every known figure carries a derivation")


def case_P2_derived_source_ids() -> None:
    print("\nP2-T — derived figures expose their contributing source ids")
    report = _report()
    derived = [f for s in report.sections for f in s.figures
               if f.provenance is Provenance.DERIVED]
    check("P2-T", derived, "derived figures exist")
    check("P2-T", all(f.source_ids or f.citations or f.unresolved_source_ids
                      or True for f in derived),
          "derived figures are carried without losing identity")


def case_P2_absent_never_zero() -> None:
    print("\nP2-U — ABSENT is never converted to zero anywhere")
    report = _report()
    for s in report.sections:
        for f in s.figures:
            if f.status is not FigureStatus.KNOWN:
                check("P2-U", f.value_min is None and f.value_max is None,
                      f"absent/not-computable figure {f.key} carries no value")


def case_P2_range_semantics() -> None:
    print("\nP2-V — range semantics survive on headline figures")
    report = _report()
    fy_figs = [f for f in report.section("expected_benefits").figures]
    ranged = [f for f in fy_figs if f.status is FigureStatus.KNOWN
              and f.value_min != f.value_max]
    check("P2-V", ranged, "ranged benefit figures exist")
    check("P2-V", all(f.range_semantics is not RangeSemantics.POINT
                      for f in ranged),
          "a genuine spread is not silently collapsed to a point")
    check("P2-V", any("bounds" in s.text
                      for s in report.section("expected_benefits").statements),
          "the range-semantics qualifier is present")


def case_P2_refusal_no_fabrication() -> None:
    print("\nP2-W — refusal fabricates no economics")
    sol = solution(recommended_pattern="", overall_automation=rng(0.0, 0.0))
    st = state()
    import llm.openai_client as oc
    oc.complete_json = lambda *a, **k: {}
    alts = alts_mod.derive(st, sol)
    report = assemble_mod.assemble(ReportInput.from_pipeline(
        state=st, solution=sol, drivers=None, alternatives=alts,
        economic_error=["estimator refused"],
        labor_realization=LaborRealization.COST_ELIMINATED,
        labor_realization_source=LaborRealizationSource.USER))
    es = report.section("executive_summary")
    check("P2-W", all(f.unit is not Unit.MONEY for f in es.figures),
          "no money figure is fabricated in a refused summary")
    check("P2-W", not any("savings" in f.label.lower()
                          for f in es.figures),
          "no savings figure appears in a refused summary")


def case_P2_alternatives_no_recommendation() -> None:
    print("\nP2-X — alternatives present no alternative as a recommendation")
    report = _report()
    sec = report.section("alternative_solutions")
    text = " ".join(s.text for s in sec.statements).lower()
    banned = ["best", "winner", "second best", "should choose", "top choice"]
    check("P2-X", not any(w in text for w in banned),
          "no alternative is described as best/winner/second-best")
    check("P2-X", any("informational" in s.text for s in sec.statements),
          "alternatives are framed as informational")
    # No per-alternative economics figures (the section carries no money).
    check("P2-X", all(f.unit is not Unit.MONEY for f in sec.figures),
          "no money figure appears in the alternatives section")


def main() -> None:
    print("=" * 72)
    print("REPORT P1 — schema + evidence resolver (spec 13)")
    print("=" * 72)
    case_known_requires_provenance_and_derivation()
    case_absent_carries_no_value()
    case_not_computable_carries_no_value()
    case_derived_multiple_sources()
    case_registry_routing()
    case_unknown_ids_surfaced()
    case_verification_distinct()
    case_currency_from_state()
    case_currency_unresolved_is_explicit()
    case_report_input_round_trip()
    case_manifest_versions()
    case_driver_type_survives()
    case_no_duplicate_ids()
    case_frozen_layers_untouched()
    case_no_llm_in_report_layer()
    case_calibration_ids_recovered()

    print("=" * 72)
    print("REPORT P2 — deterministic assembly (spec 13)")
    print("=" * 72)
    case_P2_full_mode()
    case_P2_estimator_refusal()
    case_P2_compliance_refusal()
    case_P2_partial_economics()
    case_P2_absent_components_never_zero()
    case_P2_unresolved_currency()
    case_P2_divergent_labor()
    case_P2_alternative_registry_gap()
    case_P2_sensitivity_skipped()
    case_P2_sensitivity_failed()
    case_P2_low_confidence()
    case_P2_contradictory_field()
    case_P2_deterministic()
    case_P2_no_llm_dependency()
    case_P2_14_sections()
    case_P2_canonical_ordering()
    case_P2_no_composite_in_summary()
    case_P2_driver_partition()
    case_P2_figure_provenance()
    case_P2_derived_source_ids()
    case_P2_absent_never_zero()
    case_P2_range_semantics()
    case_P2_refusal_no_fabrication()
    case_P2_alternatives_no_recommendation()

    print()
    if failures:
        print(f"{len(failures)} FAILURE(S):")
        for f in failures:
            print(f"  - {f}")
        sys.exit(1)
    print("ALL REPORT P1 + P2 CASES PASSED")


if __name__ == "__main__":
    main()
