"""Benchmark cross-check — spec 8.8.

A benchmark is a sanity check on the calculated baseline, NEVER an additive
cost: sector cost-per-unit figures already contain the same labor the baseline
computes, so adding them would double-count.
"""

from __future__ import annotations

from typing import Optional

from pydantic import BaseModel

from calc.models import midpoint
from lib.benchmarks import figure as benchmark_figure
from schemas.assessment_state import RangeEstimate, Sector

# Sector -> the pack figure comparable to a calculated cost per unit.
_UNIT_COST_KEYS = {
    Sector.DOCUMENT_PROCESSING: "cost_per_invoice_average",
    Sector.CUSTOMER_SUPPORT: "cost_per_ticket_north_america",
}


class BenchmarkCrossCheck(BaseModel):
    available: bool
    calculated_unit_cost: Optional[RangeEstimate] = None
    benchmark: Optional[RangeEstimate] = None
    benchmark_citation: str = ""
    benchmark_provenance: str = ""
    verdict: str = ""
    statement: str = ""


def cross_check(
    sector: Sector, calculated_unit_cost: Optional[RangeEstimate],
) -> BenchmarkCrossCheck:
    key = _UNIT_COST_KEYS.get(sector)
    fig = benchmark_figure(sector, key) if key else None
    if calculated_unit_cost is None or fig is None:
        return BenchmarkCrossCheck(
            available=False,
            statement="no comparable benchmark figure for this sector" if fig is None
                      else "no calculated unit cost to compare")

    bench = fig.as_range()
    calc_mid = midpoint(calculated_unit_cost)
    if calc_mid < bench.min:
        verdict = "below benchmark"
    elif calc_mid > bench.max:
        verdict = "above benchmark"
    else:
        verdict = "within benchmark range"

    caveat = ""
    if fig.provenance != "sourced":
        caveat = (f" Note: this benchmark is tagged '{fig.provenance}', not "
                  f"sourced — it is not a firm comparison.")

    return BenchmarkCrossCheck(
        available=True, calculated_unit_cost=calculated_unit_cost, benchmark=bench,
        benchmark_citation=fig.citation(), benchmark_provenance=fig.provenance,
        verdict=verdict,
        statement=(f"Calculated cost is {calculated_unit_cost.min:,.2f}-"
                   f"{calculated_unit_cost.max:,.2f} per unit; benchmark is "
                   f"{bench.min:,.2f}-{bench.max:,.2f} ({fig.citation()}). "
                   f"Assessment: {verdict}. Used for comparison only, never "
                   f"added to the baseline.{caveat}"),
    )
