"""Deterministic report assembly — spec 13.  [PRESENTATION LAYER]

    ReportInput  ->  Report

Composition only. This module performs NO analysis: it does not recalculate
economics or scores, re-rank drivers, select architectures or alternatives,
model alternative economics, infer a missing value, midpoint a range, mint a
benchmark or a citation, decide compliance, choose a labor realization policy,
compute a threshold crossing, or call an LLM. Every number it presents was
computed by a frozen layer and is carried across with its provenance intact.

TWO RULES THAT SHAPE EVERYTHING BELOW
-------------------------------------
1. Every number the assembler puts in front of a reader is a `Figure`, never
   text. A figure carries status, provenance, range semantics, derivation and
   citations; a formatted string carries none of that and cannot be validated.
   The one exception is a statement carried VERBATIM from upstream (the
   engine's payback statement, a driver statement, a consistency verdict) —
   the spec requires those to be reproduced rather than rewritten, so they keep
   whatever figures their author wrote. Those are tagged `verbatim_from`, and
   every statement the assembler authors itself is digit-free by construction.

2. Absence is carried, never smoothed. An absent cost line stays absent, a
   not-computable score stays not-computable, and each one produces a typed
   `Gap` naming its consequence. A gap is not suppressed because some other
   number is available.

DETERMINISM
-----------
`assemble(bundle) == assemble(bundle)`. No timestamp enters the sections —
`generated_at` lives only on the manifest and is supplied by the caller. Every
mapping is iterated in a sorted or upstream-defined order; nothing depends on
dict insertion order, the filesystem, or a random id.
"""

from __future__ import annotations

from typing import Any, Iterable, Optional

from report import evidence as ev
from report.schema import (
    FLAG_CURRENCY_UNRESOLVED,
    FLAG_PROVENANCE_UNKNOWN,
    Cell,
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
    ReportTable,
    Statement,
    Unit,
    ValidationItem,
)
from schemas.assessment_state import Provenance, RangeEstimate

# ---------------------------------------------------------------------------
# The fourteen canonical sections (spec 13.2). Numbers and titles are fixed;
# `layer` is the presentation grouping, which never changes the numbering.
# ---------------------------------------------------------------------------

CANONICAL_SECTIONS: tuple[tuple[int, str, str, int], ...] = (
    (1,  "executive_summary",        "Executive Summary",           1),
    (2,  "problem_definition",       "Problem Definition",          2),
    (3,  "current_process",          "Current Process",             2),
    (4,  "current_cost",             "Current Cost",                2),
    (5,  "proposed_ai_solution",     "Proposed AI Solution",        2),
    (6,  "alternative_solutions",    "Alternative Solutions",       2),
    (7,  "implementation_reqs",      "Implementation Requirements", 2),
    (8,  "ai_operating_cost",        "AI Operating Cost",           2),
    (9,  "expected_benefits",        "Expected Benefits",           2),
    (10, "risks_and_reliability",    "Risks and Reliability",       2),
    (11, "assumptions",              "Assumptions",                 3),
    (12, "external_sources",         "External Sources",            3),
    (13, "sensitivity_analysis",     "Sensitivity Analysis",        2),
    (14, "what_to_validate_next",    "What to Validate Next",       1),
)

# Mandatory framing the report may never drop (spec 13.5). Authored here once,
# digit-free, and asserted by the P2 suite.
QUALIFIERS = {
    "not_a_recommendation": (
        "This is not a recommendation to build. It is a decision-support "
        "summary of what the analysis calculated; the decision remains yours."),
    "cost_savings_only": (
        "Cost savings only. Productivity, revenue, quality and capacity "
        "benefits are not modelled and are not included in any figure here."),
    "first_year": (
        "A first-year view. This is not ROI and not a multi-year business case."),
    "confidence_not_quality": (
        "Confidence describes how well-grounded this analysis is, not whether "
        "the opportunity is a good one. A confident assessment can describe a "
        "poor case, and an uncertain one a promising case."),
    "range_semantics": (
        "Ranges here are bounds, not confidence intervals. They are the widest "
        "defensible span, because the arithmetic assumes the inputs move "
        "together."),
    "benchmarks_compare": (
        "Benchmarks are a cross-check. They are never added to the calculated "
        "figures, because they may describe the same underlying costs."),
    "alternatives_informational": (
        "Alternatives are informational. They are not ranked by preference, no "
        "separate economics were modelled for them, and none of them is a "
        "recommendation."),
    "sensitivity_not_threshold": (
        "Sensitivity at each variable's declared bounds. These are not decision "
        "thresholds, and no crossing point is calculated."),
    "sensitivity_vs_drivers": (
        "This answers how far the metric moves. Which variables matter most is "
        "a different question, answered by the Decision Drivers using a "
        "different measure — the two orderings are not expected to agree."),
    "absent_is_not_zero": (
        "Components marked as not collected are excluded from the total. The "
        "total is therefore a floor, not a complete figure."),
}


class _Ctx:
    """Assembly scratch space: the bundle, the evidence index, and the ledger."""

    def __init__(self, bundle: ReportInput):
        self.b = bundle
        self.index = ev.build_index()
        self.currency = ev.resolve_currency(bundle.state)
        self.ledger: list[Figure] = []
        self.gaps: list[Gap] = []
        self.usage: dict[str, list[str]] = {}     # evidence_id -> figure keys

    # -- figures ----------------------------------------------------------

    def keep(self, figure: Figure) -> Figure:
        """Record a figure in the ledger and index its evidence usage."""
        self.ledger.append(figure)
        for evidence_id in figure.source_ids:
            self.usage.setdefault(evidence_id, [])
            if figure.key not in self.usage[evidence_id]:
                self.usage[evidence_id].append(figure.key)
        return figure

    def from_range(
        self, key: str, label: str, r: RangeEstimate, *, unit: Unit,
        origin: str, semantics: RangeSemantics = RangeSemantics.ENVELOPE,
        flags: Optional[list[str]] = None,
    ) -> Figure:
        """Carry an upstream RangeEstimate across, resolving its evidence.

        Provenance, confidence and derivation come from the value itself.
        Evidence ids come from the value's own `source_id` plus any calibration
        id the calibration registry wrote into its own citation string.
        """
        currency = self.currency.currency if unit is Unit.MONEY else None
        figure = Figure.from_range(key, label, r, unit=unit, origin_module=origin,
                                   range_semantics=semantics, currency=currency,
                                   flags=list(flags or []))
        resolution = self.index.resolve_objects(r)
        figure = figure.model_copy(update={"source_ids": resolution.source_ids})
        return self.keep(self.index.decorate(figure))

    def absent(self, key: str, label: str, reason: str, **kw: Any) -> Figure:
        return self.keep(Figure.absent(key, label, reason, **kw))

    def not_computable(self, key: str, label: str, missing: list[str]) -> Figure:
        return self.keep(Figure.not_computable(key, label, missing))

    def category(self, key: str, label: str, value: str, *, derivation: str,
                 provenance: Optional[Provenance], origin: str) -> Figure:
        flags = [] if provenance is not None else [FLAG_PROVENANCE_UNKNOWN]
        return self.keep(Figure.category(
            key, label, value_text=value, derivation=derivation,
            provenance=provenance, origin_module=origin, flags=flags))

    def number(self, key: str, label: str, value: float, *, unit: Unit,
               derivation: str, provenance: Optional[Provenance], origin: str,
               ) -> Figure:
        flags = [] if provenance is not None else [FLAG_PROVENANCE_UNKNOWN]
        if unit is Unit.MONEY and not self.currency.currency:
            flags.append(FLAG_CURRENCY_UNRESOLVED)
        return self.keep(Figure.known(
            key, label, value_min=value, value_max=value, unit=unit,
            derivation=derivation, provenance=provenance,
            range_semantics=RangeSemantics.POINT, origin_module=origin,
            currency=(self.currency.currency if unit is Unit.MONEY else None),
            flags=flags))

    # -- gaps -------------------------------------------------------------

    def gap(self, kind: GapKind, label: str, detail: str, consequence: str) -> Gap:
        g = Gap(kind=kind, label=label, detail=detail, consequence=consequence)
        if g not in self.gaps:
            self.gaps.append(g)
        return g


def _cost_line_figure(ctx: _Ctx, prefix: str, line: Any, origin: str) -> Figure:
    """One CostLine -> one Figure, preserving ABSENT as absence."""
    key = f"{prefix}.{line.key}"
    if line.status.value == "absent" or line.amount is None:
        return ctx.absent(key, line.label, line.note or "not collected")
    figure = ctx.from_range(key, line.label, line.amount, unit=Unit.MONEY,
                            origin=origin)
    if line.note:
        figure = figure.model_copy(update={"unit_detail": line.note})
        ctx.ledger[-1] = figure
    return figure


def _breakdown(ctx: _Ctx, prefix: str, breakdown: Any, origin: str,
               gap_kind: GapKind) -> tuple[list[Figure], Figure, list[str]]:
    """A CostBreakdown -> figures + total, with absences turned into gaps."""
    figures = [_cost_line_figure(ctx, prefix, line, origin)
               for line in breakdown.lines]
    total = ctx.from_range(f"{prefix}.total", f"{breakdown.label} — total",
                           breakdown.total(), unit=Unit.MONEY, origin=origin)
    notes = [breakdown.completeness_note()]
    for line in breakdown.absent_lines:
        ctx.gap(gap_kind, f"{line.label} not included",
                line.note or "not collected",
                f"{breakdown.label} excludes this component, so the total is a "
                f"floor rather than a complete figure")
    return figures, total, notes


def _table(key: str, label: str, columns: list[str],
           rows: Iterable[list[Cell]], note: str = "") -> ReportTable:
    return ReportTable(key=key, label=label, columns=columns,
                       rows=[list(r) for r in rows], note=note)


def _text(value: Any) -> str:
    return "" if value is None else str(value)


# ---------------------------------------------------------------------------
# Mode
# ---------------------------------------------------------------------------

def determine_mode(bundle: ReportInput) -> tuple[ReportMode, str]:
    """Read the mode off the upstream terminal states. No new refusal rule.

    Refusal is what the ESTIMATOR already decided (an empty recommended pattern,
    or a hard compliance gap). Partial is what the ENGINE and SCORES already
    reported (an EconomicInputError, or a score that is not computable).
    """
    solution = bundle.solution
    if solution.compliance_gap:
        return ReportMode.REFUSED, (
            solution.compliance_statement
            or "a hard compliance requirement could not be satisfied from "
               "implementation-specific evidence")
    if not solution.recommended_pattern:
        reasons = solution.needs_more_information or solution.confidence_notes
        return ReportMode.REFUSED, (
            "; ".join(reasons) if reasons else
            "the estimator refused: no architecture was selected")

    if bundle.drivers is None or bundle.economics is None or bundle.scores is None:
        return ReportMode.PARTIAL, "; ".join(bundle.economic_error)

    incomputable = [s.label for s in (bundle.scores.economic,
                                      bundle.scores.feasibility,
                                      bundle.scores.risk) if not s.computable]
    if incomputable:
        return ReportMode.PARTIAL, "; ".join(f"{s} is not computable"
                                             for s in incomputable)
    if solution.needs_more_information:
        return ReportMode.PARTIAL, "; ".join(solution.needs_more_information)
    return ReportMode.FULL, ""


# ---------------------------------------------------------------------------
# 2. Problem Definition
# ---------------------------------------------------------------------------

# state field -> (figure key, label, unit). Ordered explicitly, so the section
# never depends on model-field or dict ordering.
_PROBLEM_FIELDS: tuple[tuple[str, str, str, Unit], ...] = (
    ("monthly_volume",            "problem.monthly_volume",
     "Monthly volume", Unit.COUNT),
    ("avg_time_per_unit_minutes", "problem.handling_time",
     "Average handling time per unit", Unit.MINUTES),
    ("current_headcount",         "problem.headcount",
     "People on this process", Unit.COUNT),
    ("fraction_time_on_process",  "problem.fraction_time",
     "Share of their time on this process", Unit.RATIO),
    ("fully_loaded_annual_cost",  "problem.loaded_cost",
     "Fully loaded annual cost per person", Unit.MONEY),
)


def _state_range_figure(ctx: _Ctx, field: str, key: str, label: str,
                        unit: Unit) -> Figure:
    value = ctx.b.state.get_value(field)
    if value is None:
        return ctx.absent(key, label,
                          "not collected during the interview — carried as "
                          "absent rather than assumed")
    return ctx.from_range(key, label, value, unit=unit,
                          origin="schemas.assessment_state",
                          semantics=RangeSemantics.SCENARIO)


def _section_problem(ctx: _Ctx) -> ReportSection:
    state = ctx.b.state
    statements = [
        Statement.code("What this assessment covers, as described in the "
                       "interview:"),
    ]
    if state.problem:
        statements.append(Statement.verbatim(state.problem,
                                             "AssessmentState.problem"))
    if state.process:
        statements.append(Statement.verbatim(state.process,
                                             "AssessmentState.process"))

    figures = [ctx.category("problem.sector", "Sector", state.sector.value,
                            derivation="selected at the start of the interview",
                            provenance=state.get_tag("sector") or Provenance.USER_PROVIDED,
                            origin="schemas.assessment_state")]
    for field, key, label, unit in _PROBLEM_FIELDS:
        figures.append(_state_range_figure(ctx, field, key, label, unit))

    if state.geography:
        figures.append(ctx.category(
            "problem.geography", "Geography", state.geography,
            derivation="collected in the interview; sets labor rates and currency",
            provenance=state.get_tag("geography") or Provenance.USER_PROVIDED,
            origin="schemas.assessment_state"))
    else:
        figures.append(ctx.absent(
            "problem.geography", "Geography",
            "not collected — labor rates and currency cannot be resolved from it"))

    if state.worker_role:
        figures.append(ctx.category(
            "problem.worker_role", "Worker role (as described)",
            state.worker_role, derivation="the user's own words",
            provenance=state.get_tag("worker_role") or Provenance.USER_PROVIDED,
            origin="schemas.assessment_state"))
    if state.worker_role_canonical:
        figures.append(ctx.category(
            "problem.worker_role_canonical", "Worker role (rate-registry key)",
            state.worker_role_canonical.value,
            derivation="normalised for the labor-rate registry lookup",
            provenance=Provenance.DERIVED, origin="schemas.assessment_state"))

    tools = [t for t in (state.current_tools or []) if str(t).strip()]
    if tools:
        figures.append(ctx.category(
            "problem.current_tools", "Current tooling", ", ".join(sorted(tools)),
            derivation="systems named in the interview",
            provenance=state.get_tag("current_tools") or Provenance.USER_PROVIDED,
            origin="schemas.assessment_state"))
    else:
        figures.append(ctx.absent("problem.current_tools", "Current tooling",
                                  "no systems were named in the interview"))

    gaps: list[Gap] = []
    for figure in figures:
        if figure.status is not FigureStatus.KNOWN:
            gaps.append(ctx.gap(
                GapKind.UNRESOLVED_FIELD, f"{figure.label} not established",
                figure.absence_reason,
                "this fact is absent from the assessment and nothing downstream "
                "substitutes for it"))
    return ReportSection(
        key="problem_definition", number=2, title="Problem Definition", layer=2,
        statements=statements, figures=figures, gaps=gaps)


# ---------------------------------------------------------------------------
# 3. Current Process
# ---------------------------------------------------------------------------

def _section_current_process(ctx: _Ctx) -> ReportSection:
    state, economics = ctx.b.state, ctx.b.economics
    statements = [Statement.code(
        "How the process runs today, as collected. Quality is reported under "
        "the metric the sector actually tracks; no metric is converted into "
        "another, and none is assumed.")]
    figures: list[Figure] = []
    gaps: list[Gap] = []

    if state.current_quality_metric is not None and state.current_quality_value is not None:
        figures.append(ctx.category(
            "process.quality_metric", "Quality metric tracked",
            state.current_quality_metric.value,
            derivation="the metric named by the user, kept with its value so it "
                       "cannot be reinterpreted",
            provenance=state.get_tag("current_quality_metric") or Provenance.USER_PROVIDED,
            origin="schemas.assessment_state"))
        figures.append(ctx.from_range(
            "process.quality_value",
            f"Current {state.current_quality_metric.value}",
            state.current_quality_value, unit=Unit.RATIO,
            origin="schemas.assessment_state",
            semantics=RangeSemantics.SCENARIO))
    else:
        reason = ("no current-process quality measurement was collected, so no "
                  "like-for-like comparison against the AI side is made. This is "
                  "not a statement that the current process is error-free")
        figures.append(ctx.absent("process.quality_value",
                                  "Current process quality", reason))
        gaps.append(ctx.gap(
            GapKind.UNRESOLVED_FIELD, "Current-process quality not measured",
            reason,
            "the current-versus-AI quality comparison is unavailable, and "
            "current valid output falls back to assuming today's process meets "
            "its own bar"))

    if economics is not None:
        consistency = economics.labor_consistency
        statements.append(Statement.verbatim(
            consistency.verdict, "EconomicResult.labor_consistency.verdict"))
        statements.append(Statement.code(
            "The two labor formulations are calculated independently and used "
            "as a cross-check. They are never averaged or combined."))
        if consistency.task_based is not None:
            figures.append(ctx.from_range(
                "process.labor_task_based",
                "Labor cost — task-based formulation", consistency.task_based,
                unit=Unit.MONEY, origin="calc.labor"))
        if consistency.workforce_based is not None:
            figures.append(ctx.from_range(
                "process.labor_workforce_based",
                "Labor cost — workforce-based formulation",
                consistency.workforce_based, unit=Unit.MONEY, origin="calc.labor"))
        figures.append(ctx.category(
            "process.baseline_basis", "Baseline used", economics.baseline_basis,
            derivation="selected by the engine's stated rule, not by size",
            provenance=Provenance.DERIVED, origin="calc.labor"))
        if consistency.status.value == "divergent":
            gaps.append(ctx.gap(
                GapKind.UNRESOLVED_FIELD, "Labor formulations diverge materially",
                consistency.verdict,
                "the baseline rests on one formulation while the other implies a "
                "different figure; every downstream number moves with that choice"))

        reconciliation = economics.time_reconciliation or {}
        if reconciliation.get("statement"):
            statements.append(Statement.verbatim(
                str(reconciliation["statement"]),
                "EconomicResult.time_reconciliation.statement"))
    else:
        statements.append(Statement.code(
            "Labor cost formulations are unavailable because the economic "
            "engine could not run on this assessment."))

    return ReportSection(key="current_process", number=3, title="Current Process",
                         layer=2, statements=statements, figures=figures, gaps=gaps)


# ---------------------------------------------------------------------------
# 4. Current Cost
# ---------------------------------------------------------------------------

def _section_current_cost(ctx: _Ctx) -> ReportSection:
    economics = ctx.b.economics
    if economics is None:
        return _empty_section(ctx, 4, GapKind.NOT_COMPUTABLE_SCORE,
                              "the economic engine could not run, so no "
                              "current-cost baseline was calculated")

    figures, total, notes = _breakdown(ctx, "current_cost",
                                       economics.current_annual_cost,
                                       "calc.current_state", GapKind.ABSENT_COST)
    statements = [
        Statement.code("What the process costs today, by component. Components "
                       "that were not collected are shown as not collected and "
                       "are excluded from the total."),
        Statement.code(QUALIFIERS["absent_is_not_zero"]),
        Statement.code(QUALIFIERS["range_semantics"]),
    ]

    rows = [[Cell(text=f.label),
             Cell(figure_key=f.key),
             Cell(text=("not collected" if f.status is not FigureStatus.KNOWN
                        else (f.provenance.value if f.provenance else "provenance unknown")))]
            for f in figures]
    rows.append([Cell(text="Total (known components only)"),
                 Cell(figure_key=total.key), Cell(text="derived")])
    table = _table("current_cost.breakdown", "Current annual cost",
                   ["Component", "Amount", "Provenance"], rows,
                   note=notes[0] if notes else "")

    tables = [table]
    benchmark = economics.benchmark
    if getattr(benchmark, "available", False):
        statements.append(Statement.code(QUALIFIERS["benchmarks_compare"]))
        bench_figs = []
        if benchmark.calculated_unit_cost is not None:
            bench_figs.append(ctx.from_range(
                "current_cost.calculated_unit", "Calculated cost per unit",
                benchmark.calculated_unit_cost, unit=Unit.MONEY,
                origin="calc.benchmark_check"))
        if benchmark.benchmark is not None:
            bench_figs.append(ctx.from_range(
                "current_cost.benchmark_unit", "Industry benchmark per unit",
                benchmark.benchmark, unit=Unit.MONEY,
                origin="calc.benchmark_check"))
        if getattr(benchmark, "verdict", ""):
            statements.append(Statement.verbatim(
                benchmark.verdict, "EconomicResult.benchmark.verdict"))
        if bench_figs:
            tables.append(_table(
                "current_cost.benchmark", "Benchmark cross-check (comparison only)",
                ["Figure", "Value"],
                [[Cell(text=f.label), Cell(figure_key=f.key)] for f in bench_figs],
                note="shown separately from the breakdown above; never added to it"))
        figures = figures + bench_figs

    return ReportSection(
        key="current_cost", number=4, title="Current Cost", layer=2,
        statements=statements, figures=figures + [total], tables=tables,
        notes=notes,
        gaps=[g for g in ctx.gaps if g.kind is GapKind.ABSENT_COST
              and "Current annual cost" in g.consequence])


def _empty_section(ctx: _Ctx, number: int, gap_kind: GapKind,
                   reason: str) -> ReportSection:
    """A section that exists but has nothing to show, and says why.

    All fourteen sections are always present. A section with no content states
    the upstream reason rather than being silently dropped, so a reader can
    tell "not applicable here" from "we forgot".
    """
    _, key, title, layer = next(s for s in CANONICAL_SECTIONS if s[0] == number)
    gap = ctx.gap(gap_kind, f"{title} unavailable", reason,
                  "this section is empty because the analysis it presents was "
                  "not produced")
    return ReportSection(
        key=key, number=number, title=title, layer=layer,
        statements=[Statement.code(
            f"Not available. {reason[:1].upper()}{reason[1:]}."
            if reason else "Not available.")],
        gaps=[gap])


# ---------------------------------------------------------------------------
# 5. Proposed AI Solution
# ---------------------------------------------------------------------------

def _section_solution(ctx: _Ctx) -> ReportSection:
    solution = ctx.b.solution
    statements = [Statement.code(
        "The architecture the registry selected under the stated constraints, "
        "and what it is expected to do. Selecting an architecture is not a "
        "recommendation to build it."),
        Statement.code(QUALIFIERS["range_semantics"])]
    figures: list[Figure] = [
        ctx.category("solution.pattern", "Selected pattern",
                     solution.recommended_pattern,
                     derivation="deterministic registry filter and ranking "
                                "(solution/ranking.py)",
                     provenance=Provenance.DERIVED, origin="solution.estimator"),
    ]
    if solution.recommended_implementation:
        figures.append(ctx.category(
            "solution.implementation", "Selected implementation",
            solution.recommended_implementation,
            derivation="the implementation the ranker actually scored",
            provenance=Provenance.DERIVED, origin="solution.estimator"))

    figures.append(ctx.from_range(
        "solution.overall_automation", "Overall automation",
        solution.overall_automation, unit=Unit.PERCENT,
        origin="solution.estimator"))
    figures.append(ctx.category(
        "solution.integration_complexity", "Integration complexity band",
        solution.integration_complexity.value,
        derivation=solution.integration_basis or "derived from assessed scope",
        provenance=Provenance.DERIVED, origin="solution.scope"))
    figures.append(ctx.category(
        "solution.engineering_effort", "Engineering effort band",
        solution.engineering_effort.value,
        derivation=solution.effort_basis or "derived from assessed scope",
        provenance=Provenance.DERIVED, origin="solution.scope"))

    tables: list[ReportTable] = []
    if solution.task_automation:
        task_rows = []
        for task in solution.task_automation:
            key = f"solution.task.{task.task.replace(' ', '_')}"
            figure = ctx.from_range(key, f"Automation — {task.task}",
                                    task.estimate, unit=Unit.PERCENT,
                                    origin="solution.estimator")
            share = ctx.number(f"{key}.share", f"Workload share — {task.task}",
                               task.workload_share, unit=Unit.RATIO,
                               derivation="derived from observed handling time",
                               provenance=task.workload_share_provenance,
                               origin="solution.workload")
            figures.extend([figure, share])
            task_rows.append([Cell(text=task.task),
                              Cell(text=task.capability.value),
                              Cell(figure_key=figure.key),
                              Cell(figure_key=share.key),
                              Cell(text=task.hitl.value)])
        tables.append(_table(
            "solution.tasks", "Per-task automation and human involvement",
            ["Task", "Capability", "Automation", "Workload share", "Human role"],
            task_rows))

    for metric in solution.performance:
        figures.append(ctx.from_range(
            f"solution.performance.{metric.metric}",
            f"Expected {metric.metric}", metric.estimate, unit=Unit.PERCENT,
            origin="solution.performance"))

    gaps: list[Gap] = []
    comparison = solution.reference_comparison
    if comparison is not None:
        statements.append(Statement.code(
            "How the selection compares with the curated sector baseline:"))
        figures.append(ctx.category(
            "solution.reference_match", "Follows the sector baseline",
            "yes" if comparison.match else "no",
            derivation=(comparison.deviation_reason
                        or "the selected pattern matches the reference"),
            provenance=Provenance.DERIVED, origin="solution.ranking"))
        for condition in comparison.active_deviations:
            statements.append(Statement.verbatim(
                condition, "SolutionEstimate.reference_comparison.active_deviations"))
        for condition in comparison.unevaluated_conditions:
            gaps.append(ctx.gap(
                GapKind.UNEVALUATED_CONDITION,
                "Reference condition could not be evaluated", condition,
                "the baseline allows departing from it under this condition, but "
                "the assessment does not capture the fact it depends on, so it "
                "was neither applied nor ruled out"))
    else:
        gaps.append(ctx.gap(
            GapKind.REGISTRY_GAP, "No reference comparison available",
            "no curated reference solution was matched for this sector",
            "the selection could not be checked against a sector baseline"))

    if solution.compliance_statement:
        statements.append(Statement.verbatim(
            solution.compliance_statement, "SolutionEstimate.compliance_statement"))
    for uncertainty in solution.key_uncertainties:
        statements.append(Statement.verbatim(
            uncertainty, "SolutionEstimate.key_uncertainties"))
    for warning in solution.provenance_warnings:
        gaps.append(ctx.gap(
            GapKind.PROVENANCE_UNKNOWN, "Provenance corrected upstream", warning,
            "a value claiming a source it could not support was downgraded "
            "before it reached the economics"))

    notes = list(solution.fit_explanations)
    return ReportSection(key="proposed_ai_solution", number=5,
                         title="Proposed AI Solution", layer=2,
                         statements=statements, figures=figures, tables=tables,
                         gaps=gaps, notes=notes)


# ---------------------------------------------------------------------------
# 6. Alternative Solutions
# ---------------------------------------------------------------------------

def _section_alternatives(ctx: _Ctx) -> ReportSection:
    alternatives = ctx.b.alternatives
    statements = [Statement.code(QUALIFIERS["alternatives_informational"])]
    if alternatives.ordering_basis:
        statements.append(Statement.verbatim(
            alternatives.ordering_basis, "AlternativesResult.ordering_basis"))

    gaps: list[Gap] = []
    tables: list[ReportTable] = []
    figures: list[Figure] = []

    if not alternatives.alternatives:
        statements.append(Statement.verbatim(
            alternatives.statement or "No materially different alternative could "
                                      "be established.",
            "AlternativesResult.statement"))
    else:
        rows = []
        for alternative in alternatives.alternatives:
            complexity = alternative.comparison.implementation_complexity
            if complexity is not None:
                figures.append(ctx.category(
                    f"alternatives.{alternative.id}.complexity",
                    f"Implementation complexity — {alternative.name}",
                    complexity.value,
                    derivation=(alternative.comparison.implementation_complexity_basis
                                or "derived by the same scope model as the primary"),
                    provenance=Provenance.DERIVED, origin="solution.scope"))
            for metric in alternative.comparison.expected_automation:
                figures.append(ctx.from_range(
                    f"alternatives.{alternative.id}.{metric.metric}",
                    f"{metric.metric} — {alternative.name}", metric.estimate,
                    unit=Unit.PERCENT, origin="solution.performance"))
            if alternative.explanation:
                statements.append(Statement.verbatim(
                    alternative.explanation,
                    f"AlternativesResult.alternatives[{alternative.id}].explanation"))
            rows.append([
                Cell(text=alternative.name),
                Cell(text=alternative.comparison.approach),
                Cell(text=alternative.difference_from_primary
                     or alternative.difference_kind.value.replace("_", " ")),
                Cell(text="; ".join(alternative.comparison.strengths) or "—"),
                Cell(text="; ".join(alternative.comparison.limitations) or "—"),
                Cell(text="; ".join(alternative.comparison.when_preferable)
                     or "no deterministic condition favouring this alternative "
                        "was identified"),
            ])
        tables.append(_table(
            "alternatives.comparison", "Alternative approaches (informational)",
            ["Approach", "Architecture", "Difference from the selected solution",
             "Strengths", "Limitations", "May suit when"], rows,
            note="listed, not ranked; no economics were modelled for any of these"))

    for category in alternatives.categories_not_in_registry:
        gaps.append(ctx.gap(
            GapKind.REGISTRY_GAP, "Approach not covered by the registry", category,
            "the curated registry holds no entry for this kind of approach, so it "
            "could not be compared. That is a gap in our coverage, not a finding "
            "that the approach is unsuitable"))

    audit_notes = [f"rejected candidate {r.pattern_id or r.implementation_id}: "
                   f"{r.reason}" for r in alternatives.rejected]
    audit_notes += list(alternatives.llm_guard_notes)

    return ReportSection(key="alternative_solutions", number=6,
                         title="Alternative Solutions", layer=2,
                         statements=statements, figures=figures, tables=tables,
                         gaps=gaps, audit_notes=audit_notes)


# ---------------------------------------------------------------------------
# Decision Drivers — Layer 1, first substantive content (approved ordering).
# A PRESENTATION PARTITION, never a re-ranking. Upstream order is preserved
# exactly; only the grouping into economically-active / factual / data-coverage
# changes. `DriverClass.for_driver` reads each driver's own type and impact and
# compares nothing against anything else.
# ---------------------------------------------------------------------------

def _section_drivers(ctx: _Ctx) -> ReportSection:
    drivers = ctx.b.drivers
    statements: list[Statement] = [Statement.code(
        "Which variables move the economics, in the order the ranking module "
        "computed. This is not a second ranking — it is the same output, "
        "grouped by what each driver is.")]
    entries: list[DriverEntry] = []
    gaps: list[Gap] = []

    if drivers is None:
        statements.append(Statement.code(
            "Decision Drivers are unavailable because the economic engine could "
            "not run on this assessment."))
        return ReportSection(key="decision_drivers", number=0,
                             title="Decision Drivers", layer=1,
                             statements=statements, gaps=gaps)

    for i, d in enumerate(drivers.drivers):
        entries.append(DriverEntry(
            key=d.key, label=d.label, statement=Statement.verbatim(
                d.statement, f"DecisionDrivers.drivers[{d.key}].statement"),
            driver_type=d.driver_type.value,
            presentation_class=DriverClass.for_driver(d.driver_type.value,
                                                       d.impact),
            rank=i, impact=d.impact, dominant_quantity=d.dominant_quantity,
            confidence=d.confidence,
            uncertainty_type=d.uncertainty_type,
            relative_width=d.relative_width, uncertainty_index=d.uncertainty_index,
            evidence_notes=list(d.evidence_ids)))

    if drivers.uncertainty_statement:
        statements.append(Statement.verbatim(
            drivers.uncertainty_statement,
            "DecisionDrivers.uncertainty_statement"))

    return ReportSection(key="decision_drivers", number=0,
                         title="Decision Drivers", layer=1,
                         statements=statements, drivers=entries, gaps=gaps)


# ---------------------------------------------------------------------------
# Scores — Layer 2 indicators, never a verdict. Composite may appear here but
# never in Layer 1 (approved decision 5). A not-computable score stays
# not-computable with its inputs named.
# ---------------------------------------------------------------------------

def _score_figure(ctx: _Ctx, score: Any) -> Figure:
    key = f"scores.{score.key}"
    if not score.computable:
        fig = ctx.not_computable(key, score.label, list(score.missing_inputs))
        flags = list(getattr(score, "flags", []) or [])
        fig = fig.model_copy(update={"flags": flags})
        ctx.ledger[-1] = fig
        return fig
    fig = ctx.number(key, score.label, float(score.value or 0.0),
                     unit=Unit.SCORE,
                     derivation=(f"{score.label}: {score.band}" +
                                 (f" — {score.note}" if score.note else "")),
                     provenance=Provenance.DERIVED, origin="calc.scoring")
    flags = list(getattr(score, "flags", []) or [])
    if flags:
        fig = fig.model_copy(update={"flags": flags,
                                     "unit_detail": "; ".join(flags)})
        ctx.ledger[-1] = fig
    return fig


def _section_scores(ctx: _Ctx) -> ReportSection:
    scores = ctx.b.scores
    statements = [Statement.code(QUALIFIERS["confidence_not_quality"])]
    if scores is None:
        statements.append(Statement.code(
            "Scores are unavailable because the scoring layer could not run."))
        return ReportSection(key="scores", number=0, title="Scores", layer=2,
                             statements=statements)
    figures = [_score_figure(ctx, scores.economic),
               _score_figure(ctx, scores.feasibility),
               _score_figure(ctx, scores.risk),
               _score_figure(ctx, scores.composite)]
    statements.append(Statement.code(
        "These are indicators, not a decision. They describe how well-grounded "
        "and how internally consistent the analysis is; a low score and a high "
        "score are both consistent with any business decision."))
    return ReportSection(key="scores", number=0, title="Scores", layer=2,
                         statements=statements, figures=figures)


# ---------------------------------------------------------------------------
# 1. Executive Summary — Layer 1 frame. Slot-filled, deterministic, neutral.
# No score value, no composite. Every figure carries its provenance chip.
# ---------------------------------------------------------------------------

def _section_executive(ctx: _Ctx, mode: ReportMode, reason: str) -> ReportSection:
    state, solution, economics = ctx.b.state, ctx.b.solution, ctx.b.economics
    statements: list[Statement] = []
    figures: list[Figure] = []

    # Slot 1 — what was assessed.
    statements.append(Statement.code("What this assessment covers:"))
    statements.append(Statement.verbatim(state.problem or "the stated problem",
                                         "AssessmentState.problem"))
    figures.append(ctx.category(
        "summary.sector", "Sector", state.sector.value,
        derivation="selected at the start of the interview",
        provenance=state.get_tag("sector") or Provenance.USER_PROVIDED,
        origin="schemas.assessment_state"))

    # Refused framing first — no fabricated solution/economics.
    if mode is ReportMode.REFUSED:
        statements.append(Statement.code("The assessment could not be completed."))
        statements.append(Statement.verbatim(reason or "the assessment was refused",
                                             "report.refusal_reason"))
        statements.append(Statement.code(
            "No architecture, automation level, performance, economics, savings, "
            "payback or recommendation is presented, because the analysis that "
            "would support one was not produced."))
        return ReportSection(key="executive_summary", number=1,
                             title="Executive Summary", layer=1,
                             statements=statements, figures=figures)

    # Slot 2 — what the analysis produced.
    if solution.recommended_pattern:
        statements.append(Statement.code(
            "The analysis produced a proposed solution:"))
        figures.append(ctx.category(
            "summary.pattern", "Proposed pattern", solution.recommended_pattern,
            derivation="deterministic registry filter and ranking",
            provenance=Provenance.DERIVED, origin="solution.estimator"))
        statements.append(Statement.code(QUALIFIERS["not_a_recommendation"]))
        if solution.overall_automation is not None:
            figures.append(ctx.from_range(
                "summary.automation", "Overall automation range",
                solution.overall_automation, unit=Unit.PERCENT,
                origin="solution.estimator"))

    # Slot 3 — what matters most (economically active drivers, upstream order).
    if ctx.b.drivers is not None:
        statements.append(Statement.code("What matters most here:"))
        for d in ctx.b.drivers.drivers:
            if d.impact > 0.0 and d.driver_type.value != "data_coverage":
                statements.append(Statement.verbatim(
                    d.statement, f"DecisionDrivers.drivers[{d.key}].statement"))

    # Slot 4 — modelled economics (only in FULL/PARTIAL with economics).
    if economics is not None:
        statements.append(Statement.code("Modelled economics:"))
        figures.append(ctx.from_range(
            "summary.current_cost", "Current annual cost",
            economics.current_annual_total, unit=Unit.MONEY,
            origin="calc.engine"))
        if economics.absent_components:
            statements.append(Statement.code(QUALIFIERS["absent_is_not_zero"]))
        if economics.ai_operating_total is not None:
            figures.append(ctx.from_range(
                "summary.ai_operating_cost", "AI annual operating cost",
                economics.ai_operating_total, unit=Unit.MONEY,
                origin="calc.engine"))
        fy = economics.first_year
        if fy is not None:
            figures.append(ctx.from_range(
                "summary.annual_savings", "Annual cost savings",
                fy.annual_cost_savings, unit=Unit.MONEY, origin="calc.lifecycle"))
            if fy.payback_statement:
                statements.append(Statement.verbatim(
                    fy.payback_statement,
                    "EconomicResult.first_year.payback_statement"))
        statements.append(Statement.code(QUALIFIERS["cost_savings_only"]))
        statements.append(Statement.code(QUALIFIERS["first_year"]))
        statements.append(Statement.code(QUALIFIERS["range_semantics"]))

    # Slot 5 — labor realization policy.
    statements.append(Statement.code("Labor realization policy:"))
    statements.append(Statement.verbatim(
        economics.realization_statement if economics is not None
        else (f"{ctx.b.labor_realization.value if ctx.b.labor_realization else 'unset'}"),
        "EconomicResult.realization_statement"))

    # Slot 6 — confidence.
    if ctx.b.confidence is not None:
        statements.append(Statement.code(
            f"Overall assessment confidence: {ctx.b.confidence.level}."))
        for r in ctx.b.confidence.reasons[:3]:
            statements.append(Statement.code(r))
    statements.append(Statement.code(QUALIFIERS["confidence_not_quality"]))

    # Slot 7 — biggest uncertainty.
    if ctx.b.drivers is not None and ctx.b.drivers.uncertainty_statement:
        statements.append(Statement.code("Biggest uncertainty:"))
        statements.append(Statement.verbatim(
            ctx.b.drivers.uncertainty_statement,
            "DecisionDrivers.uncertainty_statement"))
    elif ctx.b.solution.key_uncertainties:
        statements.append(Statement.code("Biggest uncertainty:"))
        for u in ctx.b.solution.key_uncertainties:
            statements.append(Statement.verbatim(u, "SolutionEstimate.key_uncertainties"))

    # Slot 8 — constraints and blockers.
    blockers = _gaps_by_kind(ctx, GapKind.CURRENCY_UNRESOLVED,
                             GapKind.UNRESOLVED_POLICY,
                             GapKind.BELOW_PRIMARY_VERIFICATION)
    if blockers:
        statements.append(Statement.code("Constraints and unresolved items:"))
        for g in blockers:
            statements.append(Statement.code(g.detail or g.label))

    return ReportSection(key="executive_summary", number=1,
                         title="Executive Summary", layer=1,
                         statements=statements, figures=figures)


def _gaps_by_kind(ctx: _Ctx, *kinds: GapKind) -> list[Gap]:
    wanted = set(kinds)
    return [g for g in ctx.gaps if g.kind in wanted]


# ---------------------------------------------------------------------------
# 7. Implementation Requirements
# ---------------------------------------------------------------------------

def _section_implementation(ctx: _Ctx) -> ReportSection:
    economics, solution = ctx.b.economics, ctx.b.solution
    if economics is None or economics.implementation is None:
        return _empty_section(ctx, 7, GapKind.NOT_COMPUTABLE_SCORE,
                              "the economic engine could not run, so no "
                              "implementation cost breakdown was calculated")

    statements: list[Statement] = [
        Statement.code("What it takes to build and run this solution, by stage. "
                       "The stage partition is a calibration assumption with its "
                       "version — not a project plan.")]
    figures: list[Figure] = []

    figures.append(ctx.category(
        "impl.effort_band", "Engineering effort band",
        solution.engineering_effort.value,
        derivation=solution.effort_basis or "derived from assessed scope",
        provenance=Provenance.DERIVED, origin="solution.scope"))
    figures.append(ctx.from_range(
        "impl.engineering_hours", "Engineering hours",
        solution.engineering_hours, unit=Unit.HOURS, origin="solution.estimator"))

    if economics.labor_rate_geography:
        figures.append(ctx.category(
            "impl.labor_geography", "Labor-rate geography",
            economics.labor_rate_geography,
            derivation="geography that set the implementation labor rate",
            provenance=Provenance.DERIVED, origin="calc.engine"))

    # Stages from the collected process (buy/build where recorded).
    stages = [s for s in ctx.b.state.process_stages if str(s.stage).strip()]
    table_rows: list[list[Cell]] = []
    for s in stages:
        table_rows.append([
            Cell(text=s.stage),
            Cell(text=s.buy_or_build.value.replace("_", " ")),
            Cell(text=s.vendor_or_approach or "—"),
        ])
    tables: list[ReportTable] = []
    if table_rows:
        tables.append(_table(
            "impl.stages", "Implementation stages (as collected)",
            ["Stage", "Buy/build", "Vendor or approach"], table_rows,
            note="recorded during the interview; not a worked project plan"))

    imp, total, notes = _breakdown(ctx, "impl", economics.implementation,
                                   "calc.implementation", GapKind.ABSENT_COST)
    figures += imp
    figures.append(total)

    return ReportSection(key="implementation_reqs", number=7,
                         title="Implementation Requirements", layer=2,
                         statements=statements, figures=figures,
                         tables=tables, notes=notes)


# ---------------------------------------------------------------------------
# 8. AI Operating Cost
# ---------------------------------------------------------------------------

def _section_ai_operating(ctx: _Ctx) -> ReportSection:
    economics = ctx.b.economics
    if economics is None or economics.ai_operating is None:
        return _empty_section(ctx, 8, GapKind.NOT_COMPUTABLE_SCORE,
                              "the economic engine could not run, so no AI "
                              "operating cost was calculated")

    statements: list[Statement] = [Statement.code(
        "What the AI solution costs to run each year, by component. Components "
        "that were not collected are shown as not collected and are excluded "
        "from the total."),
        Statement.code(QUALIFIERS["absent_is_not_zero"]),
        Statement.code(QUALIFIERS["range_semantics"])]

    figures, total, notes = _breakdown(ctx, "ai_operating",
                                       economics.ai_operating,
                                       "calc.ai_state", GapKind.ABSENT_COST)

    # Inference pricing citations, when the inference line used a priced model.
    if economics.inference_pricing_ids:
        res = ctx.index.resolve_many(economics.inference_pricing_ids)
        for c in res.citations:
            statements.append(Statement.verbatim(
                f"Inference priced from: {c.source} ({c.registry.value})",
                "EconomicResult.inference_pricing_ids"))

    # Currency mismatch: inference excluded -> this total is a floor.
    if any("currency" in w for w in economics.warnings):
        ctx.gap(GapKind.CURRENCY_UNRESOLVED, "Inference excluded for currency",
                "; ".join(w for w in economics.warnings if "currency" in w),
                "the AI operating total excludes inference and is therefore a "
                "floor in addition to any absent components")

    table_rows = [[Cell(text=f.label), Cell(figure_key=f.key),
                   Cell(text=("not collected"
                              if f.status is not FigureStatus.KNOWN
                              else (f.provenance.value if f.provenance
                                    else "provenance unknown")))]
                  for f in figures]
    table_rows.append([Cell(text="Total (known components only)"),
                       Cell(figure_key=total.key), Cell(text="derived")])
    tables = [_table("ai_operating.breakdown", "AI annual operating cost",
                     ["Component", "Amount", "Provenance"], table_rows,
                     note=notes[0] if notes else "")]

    return ReportSection(key="ai_operating_cost", number=8,
                         title="AI Operating Cost", layer=2,
                         statements=statements, figures=figures + [total],
                         tables=tables, notes=notes,
                         gaps=_gaps_by_kind(ctx, GapKind.CURRENCY_UNRESOLVED,
                                            GapKind.ABSENT_COST))


# ---------------------------------------------------------------------------
# 9. Expected Benefits — a first-year view, cost savings only, never ROI.
# ---------------------------------------------------------------------------

def _section_benefits(ctx: _Ctx) -> ReportSection:
    economics = ctx.b.economics
    if economics is None or economics.first_year is None:
        return _empty_section(ctx, 9, GapKind.NOT_COMPUTABLE_SCORE,
                              "the economic engine could not run, so no "
                              "benefits were calculated")

    fy = economics.first_year
    statements: list[Statement] = [
        Statement.code(QUALIFIERS["first_year"]),
        Statement.code(QUALIFIERS["cost_savings_only"]),
        Statement.code(QUALIFIERS["range_semantics"]),
        Statement.code("This is an analytical view of the first-year cost "
                       "position. It is not ROI, not a lifetime business case, "
                       "and not a recommendation.")]
    figures: list[Figure] = [
        ctx.from_range("benefits.annual_savings", "Expected annual cost savings",
                       fy.annual_cost_savings, unit=Unit.MONEY,
                       origin="calc.lifecycle"),
        ctx.from_range("benefits.first_year_ai_cost",
                       "First-year implementation + operating cost",
                       fy.first_year_ai_cost, unit=Unit.MONEY,
                       origin="calc.lifecycle"),
        ctx.from_range("benefits.first_year_net", "First-year net benefit",
                       fy.first_year_net_benefit, unit=Unit.MONEY,
                       origin="calc.lifecycle"),
    ]
    if fy.monthly_net_benefit is not None:
        figures.append(ctx.from_range(
            "benefits.monthly_net", "Monthly net benefit",
            fy.monthly_net_benefit, unit=Unit.MONEY, origin="calc.lifecycle"))

    if fy.payback_months is not None:
        figures.append(ctx.from_range(
            "benefits.payback", "Payback period", fy.payback_months,
            unit=Unit.MONTHS, origin="calc.lifecycle"))
    if fy.payback_statement:
        statements.append(Statement.verbatim(
            fy.payback_statement, "EconomicResult.first_year.payback_statement"))
    else:
        figures.append(ctx.not_computable(
            "benefits.payback", "Payback period",
            ["positive monthly net benefit across the first year"]))

    # Unit economics.
    ue = economics.unit_economics
    if ue is not None:
        figures.append(ctx.from_range(
            "benefits.current_unit_cost", "Current cost per valid unit",
            ue.current_unit_cost, unit=Unit.MONEY, origin="calc.lifecycle"))
        figures.append(ctx.from_range(
            "benefits.ai_unit_cost", "AI cost per valid unit",
            ue.ai_unit_cost, unit=Unit.MONEY, origin="calc.lifecycle"))
        if ue.note:
            statements.append(Statement.verbatim(
                ue.note, "EconomicResult.unit_economics.note"))

    # Freed capacity is CAPACITY under CAPACITY_RETAINED, never a saving.
    if economics.labor_realization is not None \
            and economics.labor_realization.value == "capacity_retained":
        if economics.freed_capacity_value is not None:
            figures.append(ctx.from_range(
                "benefits.freed_capacity", "Freed capacity value",
                economics.freed_capacity_value, unit=Unit.MONEY,
                origin="calc.ai_state"))
        statements.append(Statement.verbatim(
            economics.realization_statement,
            "EconomicResult.realization_statement"))
        statements.append(Statement.code(
            "The freed-capacity value above is capacity, not savings. It is not "
            "added to the cost-savings figures."))

    # Quality comparison, or its absence.
    q = economics.quality_comparison or {}
    if q.get("comparable"):
        statements.append(Statement.verbatim(
            q.get("statement") or "", "EconomicResult.quality_comparison"))
    else:
        ctx.gap(GapKind.UNRESOLVED_FIELD, "Current quality not comparable",
                q.get("statement") or "no current-process quality measurement",
                "the current-versus-AI quality comparison is unavailable, so "
                "current valid output is not assumed to be 100%")

    return ReportSection(key="expected_benefits", number=9,
                         title="Expected Benefits", layer=2,
                         statements=statements, figures=figures)


# ---------------------------------------------------------------------------
# 10. Risks and Reliability
# ---------------------------------------------------------------------------

def _section_risks(ctx: _Ctx) -> ReportSection:
    solution, economics, scores = ctx.b.solution, ctx.b.economics, ctx.b.scores
    statements: list[Statement] = [Statement.code(
        "Risks implied by the proposed architecture, each with the controls "
        "the selected implementation actually offers. Controls come from the "
        "registry, not from prose.")]
    figures: list[Figure] = []
    gaps: list[Gap] = []

    # Compliance blocker first, and un-averaged.
    if solution.compliance_gap:
        statements.append(Statement.code("A hard compliance blocker applies:"))
        statements.append(Statement.verbatim(
            solution.compliance_statement or "compliance requirement could not "
            "be satisfied", "SolutionEstimate.compliance_statement"))
        for exclusion in solution.compliance_exclusions:
            gaps.append(ctx.gap(
                GapKind.REGISTRY_GAP, "Candidate excluded for compliance",
                str(exclusion),
                "the candidate could not satisfy a hard compliance requirement"))

    # Structured risk controls, grouped by category.
    table_rows: list[list[Cell]] = []
    for rc in solution.risk_controls:
        category = str(rc.get("category", "")).replace("_", " ")
        risk = rc.get("risk") or ""
        controls = rc.get("controls") or []
        impl_controls = rc.get("implementation_controls") or []
        shown = controls + (["implementation: " + c for c in impl_controls]
                            if impl_controls else [])
        table_rows.append([Cell(text=category), Cell(text=risk),
                           Cell(text="; ".join(shown) or "—")])
    tables: list[ReportTable] = []
    if table_rows:
        tables.append(_table(
            "risks.controls", "Risks and controls",
            ["Category", "Risk", "Controls"], table_rows))

    # Risk score.
    if scores is not None:
        figures.append(_score_figure(ctx, scores.risk))

    # Reliability consequence.
    rel = economics.reliability or {} if economics is not None else {}
    if rel.get("statement"):
        statements.append(Statement.verbatim(
            rel["statement"], "EconomicResult.reliability.statement"))
    if economics is not None and any(
            l.key == "reliability_gap" and l.amount is not None
            for l in economics.ai_operating.lines):
        figures.append(ctx.from_range(
            "risks.reliability_cost", "Reliability-gap handling cost",
            next(l.amount for l in economics.ai_operating.lines
                 if l.key == "reliability_gap" and l.amount is not None),
            unit=Unit.MONEY, origin="calc.reliability"))
    elif rel.get("gap"):
        gaps.append(ctx.gap(
            GapKind.UNRESOLVED_FIELD, "Reliability gap not costable",
            rel.get("statement") or f"gap {rel.get('gap')}",
            "the reliability shortfall is a qualitative risk; no operational "
            "cost could be estimated for it"))

    for u in solution.key_uncertainties:
        statements.append(Statement.verbatim(
            u, "SolutionEstimate.key_uncertainties"))

    return ReportSection(key="risks_and_reliability", number=10,
                         title="Risks and Reliability", layer=2,
                         statements=statements, figures=figures,
                         tables=tables, gaps=gaps)


# ---------------------------------------------------------------------------
# 11. Assumptions — the audit layer. Calibration tables + assumed figures.
# ---------------------------------------------------------------------------

def _assumption_table(key: str, label: str, columns: list[str],
                      rows: list[list[Cell]]) -> ReportTable:
    return _table(key, label, columns, rows)


def _section_assumptions(ctx: _Ctx) -> ReportSection:
    from calc import calibration as econ_cal
    from calc import scoring_calibration as score_cal
    from solution import calibration as scope_cal

    statements: list[Statement] = [
        Statement.code("Every assumption the analysis rested on, so a second "
                       "person can re-run or question it. None of these is "
                       "empirical industry data — they are versioned product "
                       "calibrations.")]
    tables: list[ReportTable] = []

    def _rows_for(table_rows: list[dict], id_key: str) -> list[list[Cell]]:
        return [[Cell(text=str(r.get(id_key, ""))),
                 Cell(text=str(r.get("rationale", ""))),
                 Cell(text=str(r.get("version", ""))),
                 Cell(text=str(r.get("last_reviewed", "")))]
                for r in table_rows]

    econ_rows = econ_cal.audit_table()
    if econ_rows:
        tables.append(_assumption_table(
            "assumptions.economic", "Economic calibration",
            ["Id", "Rationale", "Version", "Last reviewed"],
            _rows_for(econ_rows, "calibration_id")))

    score_rows = score_cal.audit_table()
    if score_rows:
        tables.append(_assumption_table(
            "assumptions.scoring", "Scoring calibration",
            ["Id", "Rationale", "Version", "Last reviewed"],
            _rows_for(score_rows, "parameter_id")))

    scope_rows = scope_cal.all_calibration_params()
    if scope_rows:
        tables.append(_assumption_table(
            "assumptions.scope", "Solution/scope calibration",
            ["Id", "Rationale", "Version", "Last reviewed"],
            [[Cell(text=p.key), Cell(text=p.rationale),
              Cell(text=str(p.version)), Cell(text=str(p.last_reviewed or ""))]
             for p in scope_rows]))

    # Assumed figures actually used by this report.
    assumed_figs = [f for f in ctx.ledger
                    if f.provenance is Provenance.ASSUMED]
    if assumed_figs:
        statements.append(Statement.code(
            "Assumed figures carried into this assessment:"))
        tables.append(_assumption_table(
            "assumptions.figures", "Assumed figures used",
            ["Figure", "Value source"],
            [[Cell(text=f.label),
              Cell(text=f.derivation or (f.absence_reason or "assumed"))]
             for f in assumed_figs]))

    statements.append(Statement.code(
        f"Economic calibration version {econ_cal.CALIBRATION_VERSION}; scoring "
        f"calibration version {score_cal.SCORING_CALIBRATION_VERSION}; scope "
        f"calibration version "
        f"{scope_cal.all_calibration_params()[0].version if scope_rows else 'n/a'}."))

    return ReportSection(key="assumptions", number=11, title="Assumptions",
                         layer=3, statements=statements, tables=tables)


# ---------------------------------------------------------------------------
# 12. External Sources — only evidence actually used by this report.
# ---------------------------------------------------------------------------

def _section_external(ctx: _Ctx) -> ReportSection:
    statements: list[Statement] = [
        Statement.code("Sources this report actually drew on, grouped by "
                       "registry, each with its verification tier. Only "
                       "sources that contributed to this assessment appear; "
                       "the repository holds others that were not used here.")]
    tables: list[ReportTable] = []
    gaps: list[Gap] = []

    # Order citations deterministically by id; group by registry.
    used_ids = {sid for figs in ctx.usage.values() for sid in figs} \
        if hasattr(ctx, "usage") else set()
    # Gather every id referenced by ledger figures.
    used_ids = {sid for f in ctx.ledger for sid in f.source_ids}
    citations = [c for c in ctx.index.citations.values()
                 if c.evidence_id in used_ids]
    citations.sort(key=lambda c: (c.registry.value, c.evidence_id))

    rows: list[list[Cell]] = []
    for c in citations:
        rows.append([
            Cell(text=c.evidence_id), Cell(text=c.source),
            Cell(text=c.verification or "not recorded"),
            Cell(text=c.provenance.value if c.provenance else "unknown"),
            Cell(text=c.as_of or "—"),
            Cell(text="; ".join(ctx.usage.get(c.evidence_id, []))),
        ])
    if rows:
        tables.append(_assumption_table(
            "external_sources.ledger", "Evidence used",
            ["Id", "Source", "Verification", "Provenance", "As of", "Used in"],
            rows))
    else:
        statements.append(Statement.code(
            "No external source was cited by any figure in this report."))

    # Figures whose evidence is below primary verification.
    below = [c for c in citations if c.below_primary]
    if below:
        for c in below:
            gaps.append(ctx.gap(
                GapKind.BELOW_PRIMARY_VERIFICATION,
                "Evidence verified below primary",
                f"{c.evidence_id} — {c.verification or 'no tier recorded'}",
                "this figure rests on a source that was not verified against "
                "the primary document"))

    # Pack health for the sector.
    from lib.benchmarks import load_pack
    pack = load_pack(ctx.b.state.sector)
    statements.append(Statement.verbatim(
        f"Sector benchmark pack health: {pack.health()}",
        "BenchmarkPack.health"))
    if ctx.b.state.sector.value == "customer_support":
        statements.append(Statement.code(
            "The customer-support benchmark pack is materially weaker than the "
            "document-processing pack; figures sourced from it carry lower "
            "verification and should be read with that caveat."))

    return ReportSection(key="external_sources", number=12,
                         title="External Sources", layer=3,
                         statements=statements, tables=tables, gaps=gaps)


# ---------------------------------------------------------------------------
# 13. Sensitivity — magnitude at each input's own bounds, not importance.
# ---------------------------------------------------------------------------

def _section_sensitivity(ctx: _Ctx) -> ReportSection:
    sens = ctx.b.sensitivity
    if sens is None:
        return _empty_section(ctx, 13, GapKind.NOT_COMPUTABLE_SCORE,
                              "no sensitivity sweep was produced for this "
                              "assessment")

    statements: list[Statement] = [
        Statement.code(QUALIFIERS["sensitivity_not_threshold"]),
        Statement.code(QUALIFIERS["sensitivity_vs_drivers"]),
    ]
    if sens.note:
        statements.append(Statement.verbatim(sens.note,
                                             "SensitivityReport.note"))

    figures: list[Figure] = [
        ctx.number("sensitivity.baseline", f"Baseline {sens.metric}",
                   sens.baseline, unit=Unit.SCORE,
                   derivation=f"baseline value of {sens.metric}",
                   provenance=Provenance.DERIVED, origin="calc.sensitivity"),
    ]

    rows: list[list[Cell]] = []
    gaps: list[Gap] = []
    for imp in sens.impacts:
        status = "failed" if imp.failed else ("ok" if imp.swing is not None
                                              else "skipped")
        reason = imp.failed or ""
        rows.append([
            Cell(text=imp.label),
            Cell(text=imp.bounds or imp.variable),
            Cell(text=f"{imp.low_metric:g}" if imp.low_metric is not None else "—"),
            Cell(text=f"{imp.high_metric:g}" if imp.high_metric is not None else "—"),
            Cell(text=f"{imp.swing:g}" if imp.swing is not None else "—"),
            Cell(text=status),
            Cell(text=reason or (imp.source or "")),
        ])
        if imp.failed:
            gaps.append(ctx.gap(
                GapKind.NOT_COMPUTABLE_SCORE, f"{imp.label} could not be evaluated",
                imp.failed,
                "this variable's impact could not be computed within its bounds"))

    for skipped in sens.skipped:
        rows.append([Cell(text=skipped), Cell(text="—"), Cell(text="—"),
                     Cell(text="—"), Cell(text="—"),
                     Cell(text="skipped"),
                     Cell(text="no defensible range — not swept rather than "
                               "assigned an invented one")])

    tables = [_assumption_table(
        "sensitivity.table", f"Sensitivity of {sens.metric}",
        ["Variable", "Bounds", "Low outcome", "High outcome", "Swing",
         "Status", "Reason"],
        rows)] if rows else []

    return ReportSection(key="sensitivity_analysis", number=13,
                         title="Sensitivity Analysis", layer=2,
                         statements=statements, figures=figures,
                         tables=tables, gaps=gaps)


# ---------------------------------------------------------------------------
# 14. What to Validate Next — deterministic, inherits driver impact.
# ---------------------------------------------------------------------------

# Trigger -> (key, gap_kind, title). Nothing invented; each maps to an upstream
# finding.
def _validation_items(ctx: _Ctx) -> list[ValidationItem]:
    items: list[ValidationItem] = []
    seen: set[str] = set()

    def add(key: str, kind: GapKind, title: str, missing: str, why: str,
            what: str, impact: Optional[float]) -> None:
        if key in seen:
            return
        seen.add(key)
        items.append(ValidationItem(
            key=key, title=title, what_is_missing=missing, why_it_matters=why,
            what_to_collect=what, gap_kind=kind, impact=impact))

    # Uncertainty callout -> measure that variable.
    if ctx.b.drivers is not None:
        callout = ctx.b.drivers.uncertainty_callout
        if callout is not None:
            add("validate.uncertainty", GapKind.UNRESOLVED_FIELD,
                "Measure the most uncertain variable",
                f"the most uncertain driver is {callout.label}",
                callout.statement, "obtain a narrower range for this input",
                callout.impact)
        # Top drivers whose provenance is estimated/assumed -> validate input.
        for d in ctx.b.drivers.drivers:
            if d.impact > 0 and d.provenance in ("estimated", "assumed"):
                add(f"validate.driver.{d.key}", GapKind.UNRESOLVED_FIELD,
                    f"Validate the {d.label} input",
                    f"{d.label} rests on an {d.provenance} value",
                    d.statement,
                    "collect a measured or sourced value for this input",
                    d.impact)

    # Absent cost components.
    if ctx.b.economics is not None:
        for comp in ctx.b.economics.absent_components:
            add(f"validate.cost.{comp}", GapKind.ABSENT_COST,
                "Collect a missing cost component",
                f"{comp} was not collected", "it is excluded, so the total is "
                "a floor", "supply a value or confirm it is genuinely zero",
                None)

    # Missing score inputs.
    if ctx.b.scores is not None:
        for score in (ctx.b.scores.economic, ctx.b.scores.feasibility,
                      ctx.b.scores.risk):
            for mi in score.missing_inputs:
                add(f"validate.score.{score.key}.{mi}",
                    GapKind.NOT_COMPUTABLE_SCORE,
                    f"Supply input for the {score.label} score",
                    mi, f"the {score.label} score is not computable without it",
                    "collect this input", None)

    # Estimator needs more information.
    for nmi in ctx.b.solution.needs_more_information:
        add(f"validate.nmi.{nmi}", GapKind.UNRESOLVED_FIELD,
            "Resolve an unanswered estimator question", nmi,
            "the estimator flagged this as needed before a reliable answer",
            "provide the missing information", None)

    # Unevaluated reference conditions.
    if ctx.b.solution.reference_comparison is not None:
        for cond in ctx.b.solution.reference_comparison.unevaluated_conditions:
            add(f"validate.ref.{cond}", GapKind.UNEVALUATED_CONDITION,
                "Evaluate an unevaluated reference condition", cond,
                "the baseline could not be applied or ruled out for this "
                "condition", "capture the fact it depends on", None)

    # Divergent labor formulations.
    if ctx.b.economics is not None \
            and ctx.b.economics.labor_consistency.status.value == "divergent":
        add("validate.labor", GapKind.UNRESOLVED_FIELD,
            "Reconcile the two labor views",
            "the task-based and workforce-based labor formulations diverge",
            ctx.b.economics.labor_consistency.verdict,
            "reconcile or explain the divergence", None)

    # Sub-primary evidence.
    used_ids = {sid for f in ctx.ledger for sid in f.source_ids}
    for c in ctx.index.citations.values():
        if c.evidence_id in used_ids and c.below_primary:
            add(f"validate.ev.{c.evidence_id}",
                GapKind.BELOW_PRIMARY_VERIFICATION,
                "Obtain the primary source",
                f"{c.evidence_id} is verified {c.verification or 'not recorded'}",
                "a figure rests on this source; its tier is below primary",
                "obtain the primary document", None)

    # Missing quality comparison.
    q = (ctx.b.economics.quality_comparison or {}) if ctx.b.economics is not None else {}
    if not q.get("comparable"):
        add("validate.quality", GapKind.UNRESOLVED_FIELD,
            "Measure current-process quality",
            "no current-quality measurement was collected",
            "the current-versus-AI quality comparison is unavailable",
            "collect the sector's quality metric", None)

    # Compliance gaps.
    if ctx.b.solution.compliance_gap:
        add("validate.compliance", GapKind.REGISTRY_GAP,
            "Obtain the compliance attestation",
            "a hard compliance requirement is unsatisfied",
            ctx.b.solution.compliance_statement or "compliance cannot be shown",
            "obtain the required attestation or evidence", None)
    for verdict in ctx.b.solution.compliance_verdicts:
        status = str(verdict.get("status", "unknown"))
        if status in ("unknown", "unsupported"):
            add(f"validate.comp.{verdict.get('standard','')}", GapKind.REGISTRY_GAP,
                "Resolve a compliance requirement",
                f"{verdict.get('standard')} is {status}",
                "an unbacked claim cannot satisfy a requirement",
                "obtain the attestation", None)

    # Unresolved realization policy.
    if ctx.b.labor_realization is None:
        add("validate.realization", GapKind.UNRESOLVED_POLICY,
            "Confirm the labor realization policy",
            "no capacity policy was chosen",
            "the headline economics depend on whether freed labor is treated "
            "as savings or capacity",
            "choose cost-eliminated or capacity-retained", None)

    # Unresolved currency.
    if not ctx.currency.resolved:
        add("validate.currency", GapKind.CURRENCY_UNRESOLVED,
            "Resolve the currency",
            ctx.currency.basis,
            "money figures render without a currency unit",
            "confirm the geography or currency", None)

    # Rank: items with a driver impact first; ties by fixed gap-kind order.
    order = {GapKind.REGISTRY_GAP: 0, GapKind.ABSENT_COST: 1,
             GapKind.UNRESOLVED_FIELD: 2, GapKind.UNRESOLVED_POLICY: 3,
             GapKind.NOT_COMPUTABLE_SCORE: 4,
             GapKind.BELOW_PRIMARY_VERIFICATION: 5,
             GapKind.CURRENCY_UNRESOLVED: 6, GapKind.UNEVALUATED_CONDITION: 7}
    items.sort(key=lambda i: ((0 if i.impact is not None else 1),
                              order.get(i.gap_kind, 99), i.key))
    return items


def _section_validate_next(ctx: _Ctx) -> ReportSection:
    items = _validation_items(ctx)
    statements: list[Statement] = [Statement.code(
        "What to validate before relying on this analysis. Each item names "
        "what is missing, why it matters, and what to collect. Nothing here is "
        "a new scoring formula.")]
    figures: list[Figure] = []
    table_rows = []
    for item in items:
        table_rows.append([
            Cell(text=item.title),
            Cell(text=item.what_is_missing),
            Cell(text=item.why_it_matters),
            Cell(text=item.what_to_collect),
            Cell(text=(f"{item.impact:.3g}" if item.impact is not None else "—")),
        ])
        figures.append(ctx.not_computable(
            item.key, item.title, [item.what_is_missing]))
    tables = [_assumption_table(
        "validate.table", "What to validate next",
        ["Item", "What is missing", "Why it matters", "What to collect",
         "Driver impact"], table_rows)] if table_rows else []
    if not items:
        statements.append(Statement.code(
            "No unresolved validation items were identified for this "
            "assessment."))
    return ReportSection(key="what_to_validate_next", number=14,
                         title="What to Validate Next", layer=1,
                         statements=statements, figures=figures,
                         tables=tables)


# ---------------------------------------------------------------------------
# Manifest
# ---------------------------------------------------------------------------

def _build_manifest(ctx: _Ctx, mode: ReportMode) -> ReportManifest:
    from lib.benchmarks import load_pack
    from calc import calibration as econ_cal
    from calc import scoring_calibration as score_cal
    from solution import calibration as scope_cal

    pack = load_pack(ctx.b.state.sector)
    solution = ctx.b.solution
    scope_params = scope_cal.all_calibration_params()
    return ReportManifest(
        generated_at="",  # supplied by the caller/orchestration layer
        sector=ctx.b.state.sector,
        pack_version=pack.pack_version,
        pack_health=pack.health(),
        economic_calibration_version=econ_cal.CALIBRATION_VERSION,
        scoring_calibration_version=score_cal.SCORING_CALIBRATION_VERSION,
        solution_calibration_version=(scope_params[0].version
                                      if scope_params else None),
        registry_pattern_id=solution.recommended_pattern,
        registry_implementation_id=solution.recommended_implementation,
        labor_realization=(ctx.b.labor_realization.value
                           if ctx.b.labor_realization else None),
        labor_realization_source=ctx.b.labor_realization_source,
        currency=ctx.currency.currency,
        currency_basis=ctx.currency.basis,
        llm_model=None, llm_used_for=[], guard_actions=[],
        figure_ledger=list(ctx.ledger))


# ---------------------------------------------------------------------------
# Entry point — deterministic, no LLM, no engine call.
# ---------------------------------------------------------------------------

def assemble(bundle: ReportInput) -> Report:
    """Convert one frozen ReportInput into one fully-renderable Report.

    Deterministic: `assemble(b) == assemble(b)` byte-for-byte (the manifest's
    `generated_at` is left empty here and supplied by the orchestration layer,
    so no timestamp enters equality-sensitive content).
    """
    ctx = _Ctx(bundle)
    mode, reason = determine_mode(bundle)
    sections: list[ReportSection] = []

    # Layer 1 — frame first, then the first substantive content, then the
    # close. Approved decision 18.1: Executive Summary keeps position 1 as a
    # frame; Decision Drivers are the first substantive content; What to
    # Validate Next closes Layer 1.
    sections.append(_section_executive(ctx, mode, reason))
    sections.append(_section_drivers(ctx))

    # Analysis — Layer 2, in the approved order (spec §4). Alternatives (§6)
    # and Sensitivity (§13) follow the risk section.
    sections.append(_section_problem(ctx))
    sections.append(_section_current_process(ctx))
    sections.append(_section_current_cost(ctx))

    if mode is not ReportMode.REFUSED:
        sections.append(_section_solution(ctx))
        sections.append(_section_implementation(ctx))
        sections.append(_section_ai_operating(ctx))
        sections.append(_section_benefits(ctx))
        sections.append(_section_risks(ctx))
        sections.append(_section_alternatives(ctx))
        sections.append(_section_scores(ctx))
        sections.append(_section_sensitivity(ctx))

    # Audit — Layer 3.
    sections.append(_section_assumptions(ctx))
    sections.append(_section_external(ctx))

    # Layer 1 close.
    sections.append(_section_validate_next(ctx))

    # A report whose currency could not be resolved files the gap once, in the
    # summary, so it cannot be missed (approved decision 4: unresolved currency
    # is exposed, never silently defaulted).
    if not ctx.currency.resolved:
        ctx.gap(GapKind.CURRENCY_UNRESOLVED, "Currency unresolved",
                ctx.currency.basis,
                "money figures render without a currency unit; no symbol or "
                "convention was invented")
        exec_sum = next(s for s in sections if s.key == "executive_summary")
        exec_sum.gaps = list(exec_sum.gaps) + [
            g for g in ctx.gaps if g.kind is GapKind.CURRENCY_UNRESOLVED]

    manifest = _build_manifest(ctx, mode)
    return Report(mode=mode, refusal_reason=reason, sections=sections,
                  manifest=manifest)
