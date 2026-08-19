"""Labor rate registry — geography x role, with two distinct kinds of labor.

Next-steps spec section 1. Three rules this module exists to hold:

1. PROCESS labor and IMPLEMENTATION labor are not interchangeable. The people
   doing the assessed work and the engineers building the AI solution are
   different roles at different rates, and asking for one must never return
   the other.

2. The figures are MARKET COMPENSATION, not fully-loaded employer cost. The
   pipeline is: compensation -> hourly -> explicit employer load -> fully
   loaded. Each step stays visible.

3. No silent fallback. An unmatched geography or role returns UNRESOLVED —
   substituting another region's rate would corrupt every downstream number
   while looking perfectly precise.
"""

from __future__ import annotations

import json
from enum import Enum
from functools import lru_cache
from pathlib import Path
from typing import Literal, Optional

from pydantic import BaseModel, Field

from schemas.assessment_state import Provenance, RangeEstimate

_PATH = Path(__file__).resolve().parent.parent / "data" / "labor_rates.json"

HOURS_PER_YEAR = 2080


class LaborKind(str, Enum):
    PROCESS = "process"
    IMPLEMENTATION = "implementation"


class Money(BaseModel):
    low: float
    median: float
    high: float


class LaborRateEntry(BaseModel):
    rate_id: str
    geography: str
    currency: str
    labor_kind: LaborKind
    role: str
    role_category: str = ""
    sector: str = ""
    compensation_annual: Optional[Money] = None
    hourly_direct: Optional[Money] = None
    provenance: Literal["sourced", "assumed"]
    verification: Literal["primary_document", "search_snippet", "unverified"]
    source: str
    source_url: str = ""
    retrieved: str = ""
    sample_basis: str = ""
    notes: str = ""
    limitations: str = ""

    @property
    def is_fully_loaded(self) -> bool:
        """Only a rate stated as already-loaded skips the employer-load step."""
        return self.hourly_direct is not None

    def _confidence(self) -> str:
        return {"primary_document": "high", "search_snippet": "medium",
                "unverified": "low"}[self.verification]

    def compensation_hourly(self) -> Optional[RangeEstimate]:
        """MARKET COMPENSATION per hour. NOT fully loaded."""
        if self.hourly_direct is not None:
            m = self.hourly_direct
            return RangeEstimate(
                min=m.low, max=m.high, confidence=self._confidence(),
                provenance=(Provenance.SOURCED if self.provenance == "sourced"
                            else Provenance.ASSUMED),
                source=f"{self.role} hourly, {self.geography} ({self.currency}) — "
                       f"{self.source}",
                source_id=self.rate_id)
        if self.compensation_annual is None:
            return None
        m = self.compensation_annual
        return RangeEstimate(
            min=m.low / HOURS_PER_YEAR, max=m.high / HOURS_PER_YEAR,
            confidence=self._confidence(),
            provenance=(Provenance.SOURCED if self.provenance == "sourced"
                        else Provenance.ASSUMED),
            source=f"{self.role} market compensation, {self.geography} "
                   f"({self.currency}) — {self.source}, retrieved {self.retrieved} "
                   f"/ {HOURS_PER_YEAR}h",
            source_id=self.rate_id)

    def compensation_annual_range(self) -> Optional[RangeEstimate]:
        if self.compensation_annual is None:
            if self.hourly_direct is None:
                return None
            m = self.hourly_direct
            return RangeEstimate(
                min=m.low * HOURS_PER_YEAR, max=m.high * HOURS_PER_YEAR,
                confidence=self._confidence(), provenance=Provenance.ASSUMED,
                source=f"{self.source} x {HOURS_PER_YEAR}h", source_id=self.rate_id)
        m = self.compensation_annual
        return RangeEstimate(
            min=m.low, max=m.high, confidence=self._confidence(),
            provenance=(Provenance.SOURCED if self.provenance == "sourced"
                        else Provenance.ASSUMED),
            source=f"{self.role} market compensation, {self.geography} — {self.source}",
            source_id=self.rate_id)


class FullyLoadedMultiplier(BaseModel):
    status: Literal["unresolved", "sourced"]
    min: float
    max: float
    provenance: Literal["sourced", "assumed"]
    verification: str
    rationale: str
    source: str = ""

    def as_range(self) -> RangeEstimate:
        return RangeEstimate(
            min=self.min, max=self.max, confidence="low",
            provenance=(Provenance.SOURCED if self.provenance == "sourced"
                        else Provenance.ASSUMED),
            source=f"employer load ({self.status}): {self.rationale}")


class RateBook(BaseModel):
    version: int
    description: str = ""
    fully_loaded_multiplier: FullyLoadedMultiplier
    entries: list[LaborRateEntry] = Field(default_factory=list)
    not_yet_sourced: list[str] = Field(default_factory=list)


class RateLookup(BaseModel):
    """A lookup result. `resolved` is first-class — see rule 3."""
    resolved: bool
    entry: Optional[LaborRateEntry] = None
    statement: str = ""


@lru_cache(maxsize=1)
def load_rates() -> RateBook:
    return RateBook.model_validate(json.loads(_PATH.read_text(encoding="utf-8")))


def _norm_geo(geography: Optional[str]) -> str:
    g = (geography or "").strip().lower()
    return {"in": "india", "usa": "us", "united states": "us"}.get(g, g)


def lookup(
    geography: Optional[str], kind: LaborKind,
    role: Optional[str] = None, sector: Optional[str] = None,
) -> RateLookup:
    """Resolve a rate by geography + labor kind (+ role or sector).

    Returns UNRESOLVED rather than borrowing a different geography or role.
    """
    if not geography:
        return RateLookup(resolved=False, statement=(
            "no geography on the assessment — a labor rate cannot be resolved, and "
            "borrowing another region's rate would silently mis-cost the process"))
    geo = _norm_geo(geography)
    candidates = [e for e in load_rates().entries
                  if _norm_geo(e.geography) == geo and e.labor_kind == kind]
    if not candidates:
        return RateLookup(resolved=False, statement=(
            f"no {kind.value} labor rate for geography {geography!r}. It is NOT "
            f"substituted from another geography."))
    if role:
        exact = [e for e in candidates if e.role == role]
        if exact:
            return RateLookup(resolved=True, entry=exact[0])
        return RateLookup(resolved=False, statement=(
            f"no {kind.value} rate for role {role!r} in {geography}. Available: "
            f"{[e.role for e in candidates]}. Another role is NOT substituted."))
    if sector:
        by_sector = [e for e in candidates if e.sector in (sector, "cross_cutting")]
        if by_sector:
            return RateLookup(resolved=True, entry=by_sector[0])
        return RateLookup(resolved=False, statement=(
            f"no {kind.value} rate for sector {sector!r} in {geography}"))
    return RateLookup(resolved=True, entry=candidates[0])


def fully_loaded(entry: LaborRateEntry) -> tuple[Optional[RangeEstimate], str]:
    """Apply the employer load to market compensation, keeping both visible.

    Returns (fully_loaded_hourly, status_statement). The multiplier is
    currently UNRESOLVED, so the result is explicitly an assumption-adjusted
    figure, never presented as a sourced fully-loaded cost.
    """
    comp = entry.compensation_hourly()
    if comp is None:
        return None, "no compensation figure on this rate entry"
    if entry.is_fully_loaded:
        return comp, ("rate is stated as already fully loaded; no employer-load "
                      "adjustment applied")

    mult = load_rates().fully_loaded_multiplier
    loaded = RangeEstimate(
        min=comp.min * mult.min, max=comp.max * mult.max,
        confidence="low", provenance=Provenance.DERIVED,
        source=(f"{comp.source} x employer load {mult.min}-{mult.max}x "
                f"[{mult.status}: {mult.source or 'unsourced'}]"),
        source_id=entry.rate_id)
    return loaded, (
        f"market compensation lifted by an employer-load multiplier of "
        f"{mult.min}-{mult.max}x whose status is '{mult.status}'. The multiplier "
        f"is an explicit assumption, not a sourced figure, and the underlying "
        f"compensation remains separately auditable.")
