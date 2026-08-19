"""Lifecycle cost, unit economics, savings and payback — spec 8.6, 8.7.

Two rules this module exists to hold:

  * Unit cost is normalised on VALID output. Producing more units more cheaply
    is not an improvement if a larger share of them fail the accuracy bar.
  * Payback is reported ONLY when monthly net benefit is positive. A negative
    or absent payback is a legitimate result, not an error to be papered over
    with a large number.
"""

from __future__ import annotations

from typing import Optional

from pydantic import BaseModel

from calc.models import MONTHS_PER_YEAR, div, midpoint, money, mul, scale, sub
from schemas.assessment_state import Provenance, RangeEstimate


class UnitEconomics(BaseModel):
    current_unit_cost: Optional[RangeEstimate] = None
    ai_unit_cost: Optional[RangeEstimate] = None
    first_year_unit_cost: Optional[RangeEstimate] = None
    current_valid_output: Optional[RangeEstimate] = None
    ai_valid_output: Optional[RangeEstimate] = None
    note: str = ""


class FirstYearEconomics(BaseModel):
    implementation_cost: RangeEstimate
    ai_annual_operating_cost: RangeEstimate
    first_year_ai_cost: RangeEstimate
    annual_cost_savings: RangeEstimate
    first_year_net_benefit: RangeEstimate
    monthly_net_benefit: RangeEstimate
    payback_months: Optional[RangeEstimate] = None
    payback_statement: str = ""


def valid_output(volume: RangeEstimate, accuracy: RangeEstimate, label: str) -> RangeEstimate:
    """Annual output meeting the accuracy bar (spec 8.6)."""
    return mul(volume, accuracy, source=f"annual volume x {label} accuracy")


def unit_economics(
    current_annual_cost: RangeEstimate,
    ai_annual_operating_cost: RangeEstimate,
    first_year_ai_cost: RangeEstimate,
    annual_volume: Optional[RangeEstimate],
    current_accuracy: Optional[RangeEstimate],
    ai_accuracy: Optional[RangeEstimate],
) -> UnitEconomics:
    if annual_volume is None:
        return UnitEconomics(note="no annual volume — unit economics not computable")
    if ai_accuracy is None:
        return UnitEconomics(
            note="no expected AI accuracy available — unit cost on valid output "
                 "would be misleading, so it is omitted rather than computed on "
                 "raw volume")

    cur_valid = (valid_output(annual_volume, current_accuracy, "current")
                 if current_accuracy is not None else annual_volume)
    ai_valid = valid_output(annual_volume, ai_accuracy, "expected AI")

    note = ("unit costs are normalised on output meeting the accuracy bar, not "
            "raw throughput")
    if current_accuracy is None:
        note += ("; current valid output assumes today's process meets its own "
                 "bar (no current quality data collected)")
    return UnitEconomics(
        current_unit_cost=div(current_annual_cost, cur_valid,
                              source="current annual cost / current valid output"),
        ai_unit_cost=div(ai_annual_operating_cost, ai_valid,
                         source="AI annual operating cost / AI valid output"),
        first_year_unit_cost=div(first_year_ai_cost, ai_valid,
                                 source="first-year AI cost / AI valid output"),
        current_valid_output=cur_valid, ai_valid_output=ai_valid, note=note,
    )


def first_year_economics(
    current_annual_cost: RangeEstimate,
    ai_annual_operating_cost: RangeEstimate,
    implementation_cost: RangeEstimate,
) -> FirstYearEconomics:
    first_year = RangeEstimate(
        min=implementation_cost.min + ai_annual_operating_cost.min,
        max=implementation_cost.max + ai_annual_operating_cost.max,
        confidence="low", provenance=Provenance.DERIVED,
        source="implementation cost + AI annual operating cost",
    )
    savings = sub(current_annual_cost, ai_annual_operating_cost,
                  source="current annual cost - AI annual operating cost "
                         "(cost savings only; no productivity or revenue benefit)")
    net = sub(savings, implementation_cost,
              source="annual cost savings - implementation cost")
    monthly = scale(savings, 1.0 / MONTHS_PER_YEAR, source="annual savings / 12")

    payback, statement = _payback(implementation_cost, monthly)
    return FirstYearEconomics(
        implementation_cost=implementation_cost,
        ai_annual_operating_cost=ai_annual_operating_cost,
        first_year_ai_cost=first_year, annual_cost_savings=savings,
        first_year_net_benefit=net, monthly_net_benefit=monthly,
        payback_months=payback, payback_statement=statement,
    )


def _payback(
    implementation: RangeEstimate, monthly_net: RangeEstimate,
) -> tuple[Optional[RangeEstimate], str]:
    """Spec 8.7: only when monthly net benefit is positive."""
    if monthly_net.max <= 0:
        return None, ("No positive payback under the current assumptions — the "
                      "AI scenario does not reduce annual cost.")
    if monthly_net.min <= 0:
        best = implementation.min / monthly_net.max
        return None, (
            f"No payback can be stated: the monthly net benefit range spans zero "
            f"({monthly_net.min:,.0f} to {monthly_net.max:,.0f}), so the "
            f"assumptions admit both a payback of about {best:.0f} months and no "
            f"payback at all. Narrowing the driving estimates is required before "
            f"this number means anything.")
    payback = div(implementation, monthly_net,
                  source="implementation cost / monthly net benefit")
    # Sub-month paybacks must not render as "0-0 months" — an implementation
    # that pays back almost immediately should say so, not print a zero.
    if payback.max < 1.0:
        return payback, ("Payback in under a month under the current "
                         "assumptions — the implementation cost is small "
                         "relative to the modelled annual saving, which is "
                         "worth sanity-checking before relying on it.")
    fmt = (lambda v: f"{v:.1f}" if v < 10 else f"{v:.0f}")
    return payback, (
        f"Payback in approximately {fmt(payback.min)}-{fmt(payback.max)} months "
        f"under the current assumptions.")
