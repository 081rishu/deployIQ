"""Deterministic report rendering (P5).

Presentation only: converts a validated `Report` into Markdown and JSON. It
never recalculates analytics, never calls an LLM, never rewrites conclusions,
and never repairs invalid input. Invalid reports fail closed.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from report import validate
from report.schema import Figure, FigureStatus, RangeSemantics, Report, ReportInput, Unit


@dataclass(frozen=True)
class RenderedReport:
    markdown: str
    json_doc: dict


# Approved presentation order (keeps canonical numbering/titles as assembled,
# while placing non-canonical sections where P2 positioned them).
_RENDER_ORDER = [
    "executive_summary", "decision_drivers",
    "problem_definition", "current_process", "current_cost",
    "proposed_ai_solution", "alternative_solutions", "implementation_reqs",
    "ai_operating_cost", "expected_benefits", "risks_and_reliability",
    "scores", "sensitivity_analysis",
    "assumptions", "external_sources", "what_to_validate_next",
]


def render(report: Report, bundle: Optional[ReportInput] = None) -> RenderedReport:
    vr = validate.validate(report, bundle)
    if not vr.valid:
        raise ValueError(f"report is invalid and cannot be rendered: "
                         f"{[e.code for e in vr.errors]}")
    return RenderedReport(markdown=render_markdown(report),
                          json_doc=render_json(report, vr))


def render_json(report: Report, vr=None) -> dict:
    data = report.model_dump(mode="json")
    if vr is not None:
        data["validation"] = {
            "valid": vr.valid,
            "checked_rules": list(vr.checked_rules),
            "warnings": [w.model_dump(mode="json") for w in vr.warnings],
        }
    return data


def render_markdown(report: Report) -> str:
    sections = _ordered_sections(report)
    lines: list[str] = [
        f"# DeployIQ Final Report ({report.mode.value})",
        "",
        f"_Sector: {report.manifest.sector.value}_",
        "",
    ]
    if report.mode.value == "refused":
        lines += [f"**Refusal reason:** {report.refusal_reason}", ""]

    for section in sections:
        heading = (f"## {section.number}. {section.title}" if section.number > 0
                   else f"## {section.title}")
        lines += [heading, ""]

        if section.drivers:
            lines += _render_drivers(section)

        for statement in section.statements:
            lines.append(f"- {statement.text}")
        if section.statements:
            lines.append("")

        for figure in section.figures:
            lines.append(_render_figure(figure))
        if section.figures:
            lines.append("")

        for table in section.tables:
            lines += _render_table(table)

        if section.gaps:
            lines.append("**Gaps / limitations**")
            for gap in section.gaps:
                lines.append(f"- {gap.label}: {gap.detail}"
                             f" ({gap.consequence})")
            lines.append("")

        if section.notes:
            lines.append("**Notes**")
            for note in section.notes:
                lines.append(f"- {note}")
            lines.append("")

    return "\n".join(lines).strip() + "\n"


def _ordered_sections(report: Report):
    by_key = {s.key: s for s in report.sections}
    ordered = [by_key[k] for k in _RENDER_ORDER if k in by_key]
    # Keep any unrecognised section keys deterministic at the end.
    remainder = sorted([s for s in report.sections if s.key not in _RENDER_ORDER],
                       key=lambda s: (s.layer, s.number, s.key))
    return ordered + remainder


def _render_drivers(section) -> list[str]:
    lines = ["**Decision Drivers**"]
    groups = {
        "economically_active": "Economically active",
        "factual_input": "Factual input",
        "data_coverage": "Data coverage",
    }
    for key, label in groups.items():
        entries = [d for d in section.drivers if d.presentation_class.value == key]
        if not entries:
            continue
        lines.append(f"- **{label}**")
        for e in entries:
            lines.append(f"  - ({e.rank + 1}) {e.label}: {e.statement.text}")
    lines.append("")
    return lines


def _render_figure(f: Figure) -> str:
    if f.status is not FigureStatus.KNOWN:
        return (f"**{f.label}:** {f.status.value.replace('_', ' ').title()}"
                f" — {f.absence_reason}")

    value = _format_value(f)
    prov = f.provenance.value if f.provenance is not None else "unknown"
    verif = _verification_label(f)
    semantics = _semantics_label(f)
    pieces = [f"Provenance: {prov}", f"Verification: {verif}",
              f"Range: {semantics}"]
    if f.derivation:
        pieces.append(f"Derivation: {f.derivation}")
    if f.source_ids:
        pieces.append(f"Source IDs: {', '.join(f.source_ids)}")
    if f.unresolved_source_ids:
        pieces.append("Unresolved source IDs: "
                      + ", ".join(f.unresolved_source_ids))
    return f"**{f.label}:** {value}\n_{' · '.join(pieces)}_"


def _format_value(f: Figure) -> str:
    if f.range_semantics is RangeSemantics.CATEGORY:
        return f.value_text or "(missing category)"

    if f.value_min is None or f.value_max is None:
        return "(missing value)"

    if f.unit is Unit.PERCENT:
        if f.value_min == f.value_max:
            return f"{f.value_min:g}%"
        return f"{f.value_min:g}–{f.value_max:g}%"

    if f.unit is Unit.RATIO:
        if f.value_min == f.value_max:
            return f"{f.value_min:g}"
        return f"{f.value_min:g}–{f.value_max:g}"

    if f.unit is Unit.MONEY:
        if f.currency:
            prefix = f.currency + " "
        elif "currency_unresolved" in f.flags:
            prefix = "(currency unresolved) "
        else:
            prefix = ""
        if f.value_min == f.value_max:
            return f"{prefix}{f.value_min:,.2f}"
        return f"{prefix}{f.value_min:,.2f}–{f.value_max:,.2f}"

    suffix = {
        Unit.MONTHS: " months", Unit.HOURS: " hours", Unit.MINUTES: " minutes",
        Unit.COUNT: "", Unit.SCORE: "", Unit.TEXT: "",
        Unit.CATEGORY: "", Unit.RATIO: "", Unit.PERCENT: "%",
    }.get(f.unit, "")
    if f.value_min == f.value_max:
        return f"{f.value_min:g}{suffix}"
    return f"{f.value_min:g}–{f.value_max:g}{suffix}"


def _verification_label(f: Figure) -> str:
    if not f.citations:
        return "not recorded"
    tiers = sorted({(c.verification or "not_recorded") for c in f.citations})
    if any(t != "primary_document" for t in tiers):
        return "below-primary"
    return "primary_document"


def _semantics_label(f: Figure) -> str:
    if f.range_semantics is RangeSemantics.ENVELOPE:
        return "calculated envelope"
    if f.range_semantics is RangeSemantics.SCENARIO:
        return "scenario range"
    if f.range_semantics is RangeSemantics.POINT:
        return "point"
    return "category"


def _render_table(table) -> list[str]:
    cols = " | ".join(table.columns)
    sep = " | ".join(["---"] * len(table.columns))
    lines = [f"**{table.label}**", "", f"| {cols} |", f"| {sep} |"]
    for row in table.rows:
        vals = []
        for cell in row:
            if cell.figure_key is not None:
                vals.append(f"{{FIGURE:{cell.figure_key}}}")
            else:
                vals.append(cell.text or "")
        lines.append(f"| {' | '.join(vals)} |")
    if table.note:
        lines.append("")
        lines.append(f"_Note: {table.note}_")
    lines.append("")
    return lines
