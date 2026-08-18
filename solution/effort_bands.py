"""Deterministic engineering-effort bands (P0.2).

The LLM selects a band; this module returns the hours range and the labor
rate SEPARATELY, each with explicit provenance. Hours/cost are never invented
by the LLM.
"""

from __future__ import annotations

from schemas.assessment_state import EffortBand
from solution.schema import Provenance, RangeEstimate


# Defensible assumption-based hour ranges per band.
# These are explicit assumptions until a sourced reference table exists.
_BANDS = {
    EffortBand.SMALL: RangeEstimate(
        min=24, max=60, confidence="medium",
        provenance=Provenance.ASSUMPTION,
        source="assumption: typical small/low-code automation build",
    ),
    EffortBand.MEDIUM: RangeEstimate(
        min=80, max=200, confidence="medium",
        provenance=Provenance.ASSUMPTION,
        source="assumption: typical medium build with integration",
    ),
    EffortBand.LARGE: RangeEstimate(
        min=200, max=500, confidence="medium",
        provenance=Provenance.ASSUMPTION,
        source="assumption: large custom build with multiple integrations",
    ),
}

# Fully-loaded engineering labor rate, kept SEPARATE from hours (P0.2).
_LABOR_RATE = RangeEstimate(
    min=50, max=90, confidence="low",
    provenance=Provenance.ASSUMPTION,
    source="assumption: fully-loaded engineering rate varies by region/role",
)


_LABEL = {EffortBand.SMALL: "small", EffortBand.MEDIUM: "medium", EffortBand.LARGE: "large"}


def hours_for(band: EffortBand) -> RangeEstimate:
    return _BANDS[band]


def labor_rate() -> RangeEstimate:
    """Return the fully-loaded labor rate separately from hours."""
    return _LABOR_RATE


def cost_for(band: EffortBand) -> RangeEstimate:
    """Derived cost = hours x labor rate (both explicit)."""
    hrs = hours_for(band)
    rate = labor_rate()
    return RangeEstimate(
        min=round(hrs.min * rate.min, 0),
        max=round(hrs.max * rate.max, 0),
        confidence="low",
        provenance=Provenance.DERIVED,
        source=f"derived: {_LABEL[band]} hours ({hrs.min}-{hrs.max}) x labor rate ({rate.min}-{rate.max})",
    )
