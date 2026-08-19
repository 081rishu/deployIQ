"""Current annual cost — spec 8.2.

    Current Annual Cost =
      Attributable Labor Cost + Existing Tooling/Infrastructure
      + Error/Rework Cost + Other Direct Operating Costs

Only labor is currently obtainable: the interviewer collects nothing for the
other three components. They are therefore reported ABSENT rather than zero,
which means the computed baseline is a FLOOR. That matters directionally —
understating the current cost understates the savings, so the engine errs
against the AI case rather than for it.
"""

from __future__ import annotations

from typing import Optional

from calc.models import MONTHS_PER_YEAR, CostBreakdown, CostLine, money, mul, scale
from schemas.assessment_state import point, AssessmentState, Provenance, RangeEstimate


def _tooling(state: AssessmentState) -> CostLine:
    if point(state.annual_tooling_cost):
        return CostLine(
            key="tooling", label="Existing tooling / infrastructure",
            amount=money(float(point(state.annual_tooling_cost)),
                         provenance=Provenance.USER_PROVIDED, confidence="high",
                         source="user-provided annual tooling cost"),
            note="user-provided")
    if point(state.monthly_tooling_cost):
        return CostLine(
            key="tooling", label="Existing tooling / infrastructure",
            amount=scale(money(float(point(state.monthly_tooling_cost)),
                               provenance=Provenance.USER_PROVIDED, confidence="high",
                               source="user-provided monthly tooling cost"),
                         MONTHS_PER_YEAR, source="monthly tooling cost x 12"),
            note="derived from the monthly figure")
    return CostLine.absent("tooling", "Existing tooling / infrastructure",
                           "not provided — excluded, so the baseline is a floor")


def _rework(state: AssessmentState, annual_volume: Optional[RangeEstimate],
            hourly_rate: Optional[RangeEstimate]) -> CostLine:
    """Prefer operational inputs over a guessed annual figure (E1 5.2)."""
    if point(state.annual_rework_cost):
        return CostLine(
            key="error_rework", label="Error / rework cost",
            amount=money(float(point(state.annual_rework_cost)),
                         provenance=Provenance.USER_PROVIDED, confidence="high",
                         source="user-provided annual rework cost"),
            note="user-provided directly; operational inputs not re-derived, so "
                 "the same cost cannot be counted twice")
    if (point(state.error_rate) and point(state.rework_time_per_error_minutes)
            and annual_volume is not None and hourly_rate is not None):
        errors = scale(annual_volume, float(point(state.error_rate)),
                       source=f"annual volume x error rate {point(state.error_rate):.1%}")
        hours = scale(errors, float(point(state.rework_time_per_error_minutes)) / 60.0,
                      source=f"errors x {point(state.rework_time_per_error_minutes)} min rework")
        return CostLine(
            key="error_rework", label="Error / rework cost",
            amount=mul(hours, hourly_rate,
                       source="rework hours x labor rate"),
            note="derived from error rate and rework time")
    return CostLine.absent("error_rework", "Error / rework cost",
                           "neither an annual figure nor error-rate + rework-time "
                           "was provided")


def current_annual_cost(
    labor: RangeEstimate, state: Optional[AssessmentState] = None,
    annual_volume: Optional[RangeEstimate] = None,
    hourly_rate: Optional[RangeEstimate] = None,
) -> CostBreakdown:
    b = CostBreakdown(label="Current annual cost")
    b.lines.append(CostLine(
        key="attributable_labor", label="Attributable labor", amount=labor,
        note="the authoritative baseline (spec 8.1)"))
    if state is None:
        for key, label in (("tooling", "Existing tooling / infrastructure"),
                           ("error_rework", "Error / rework cost"),
                           ("other_operating", "Other direct operating costs")):
            b.lines.append(CostLine.absent(key, label, "no assessment state supplied"))
        return b

    b.lines.append(_tooling(state))
    b.lines.append(_rework(state, annual_volume, hourly_rate))
    if point(state.annual_other_direct_cost):
        b.lines.append(CostLine(
            key="other_operating", label="Other direct operating costs",
            amount=money(float(point(state.annual_other_direct_cost)),
                         provenance=Provenance.USER_PROVIDED, confidence="high",
                         source="user-provided other direct operating costs"),
            note=state.other_direct_cost_description or "user-provided"))
    else:
        b.lines.append(CostLine.absent(
            "other_operating", "Other direct operating costs",
            "not provided; generic corporate overhead is deliberately excluded"))
    return b
