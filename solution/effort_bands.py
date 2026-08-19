"""Deterministic engineering-effort bands (P0.2).

The LLM selects a band; this module returns the hours range and the labor
rate SEPARATELY, each with explicit provenance. Hours/cost are never invented
by the LLM.
"""

from __future__ import annotations

from typing import Optional

from lib.labor_rates import (
    LaborKind,
    LaborRateEntry,
    fully_loaded,
    lookup as rate_lookup,
)
from schemas.assessment_state import EffortBand, Provenance, RangeEstimate


# Defensible assumption-based hour ranges per band.
# These are explicit assumptions until a sourced reference table exists.
_BANDS = {
    EffortBand.SMALL: RangeEstimate(
        min=24, max=60, confidence="medium",
        provenance=Provenance.ASSUMED,
        source="assumption: typical small/low-code automation build",
    ),
    EffortBand.MEDIUM: RangeEstimate(
        min=80, max=200, confidence="medium",
        provenance=Provenance.ASSUMED,
        source="assumption: typical medium build with integration",
    ),
    EffortBand.LARGE: RangeEstimate(
        min=200, max=500, confidence="medium",
        provenance=Provenance.ASSUMED,
        source="assumption: large custom build with multiple integrations",
    ),
}

# The labor rate now lives in data/labor_rates.json with geography, currency
# and provenance (finesse spec 3). It is deliberately NOT a constant here:
# a generic per-hour figure with no region attached was the exact problem.


_LABEL = {EffortBand.SMALL: "small", EffortBand.MEDIUM: "medium", EffortBand.LARGE: "large"}


def hours_for(band: EffortBand) -> RangeEstimate:
    return _BANDS[band]


def implementation_rate(geography: Optional[str] = None) -> Optional[LaborRateEntry]:
    """The IMPLEMENTATION (engineering) rate for a geography.

    Never returns process labor: building the solution and running the process
    are different roles at different rates (next-steps spec 1.1).
    """
    found = rate_lookup(geography, LaborKind.IMPLEMENTATION)
    return found.entry if found.resolved else None


def labor_rate(geography: Optional[str] = None) -> Optional[RangeEstimate]:
    """The engineering labor rate, kept separate from hours."""
    entry = implementation_rate(geography)
    if entry is None:
        return None
    loaded, _ = fully_loaded(entry)
    return loaded


def labor_rate_entry(geography: Optional[str] = None) -> Optional[LaborRateEntry]:
    return implementation_rate(geography)


def cost_for(band: EffortBand, geography: Optional[str] = None) -> Optional[RangeEstimate]:
    """Derived cost = hours x engineering rate, both explicit and independently
    auditable. Changing the rate does not change the hours, and vice versa.

    Returns None when no implementation rate exists for the geography — no
    silent substitution from another market.
    """
    hrs = hours_for(band)
    entry = implementation_rate(geography)
    if entry is None:
        return None
    rate, _ = fully_loaded(entry)
    if rate is None:
        return None
    return RangeEstimate(
        min=round(hrs.min * rate.min, 0),
        max=round(hrs.max * rate.max, 0),
        confidence="low",
        provenance=Provenance.DERIVED,
        source=(f"derived: {_LABEL[band]} hours ({hrs.min}-{hrs.max}) x "
                f"{entry.geography} {entry.role} rate {rate.min:,.0f}-{rate.max:,.0f} "
                f"{entry.currency} [{entry.rate_id}]"),
        source_id=None,
    )
