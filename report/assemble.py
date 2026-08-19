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
