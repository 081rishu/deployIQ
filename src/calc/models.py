"""Value types and interval arithmetic for the Economic Engine (spec 8).

Every economic quantity is a `RangeEstimate` — the canonical ranged value from
schemas/assessment_state.py — so a cost carries its provenance and citation the
same way an interview answer does.

INTERVAL ARITHMETIC — READ THIS BEFORE TRUSTING A WIDTH
-------------------------------------------------------
Combining ranges here uses standard interval arithmetic: lower bounds combine
with lower bounds, upper with upper. That is correct as a GUARANTEED ENVELOPE
(the true value cannot fall outside it) but it is NOT a confidence interval: it
implicitly assumes every input's uncertainty moves together, so the envelope is
the widest defensible answer rather than the likeliest one.

This matters downstream. Spec 9.5 selects the "biggest uncertainty" callout by
range width, so a variable can look uncertain because of how many multiplied
terms sit behind it rather than because anything is genuinely unknown. Two
mitigations:

  * every derived value records `source` describing how it was combined, so a
    wide range can be traced to its arithmetic;
  * `midpoint()` gives the central estimate, and the engine reports central
    values alongside bounds rather than leading with the envelope.

Proper treatment (correlation modelling or Monte Carlo) is deliberately out of
MVP scope, and is recorded as a known limitation rather than hidden.
"""

from __future__ import annotations

from enum import Enum
from typing import Iterable, Optional

from pydantic import BaseModel, Field

from schemas.assessment_state import Provenance, RangeEstimate

HOURS_PER_YEAR = 2080          # 40h x 52w, the conventional FTE year
MONTHS_PER_YEAR = 12

# Confidence degrades as values are combined: a derived figure is never more
# confident than its weakest input.
_CONF_ORDER = {"low": 0, "medium": 1, "high": 2}
_CONF_NAME = {0: "low", 1: "medium", 2: "high"}


def weakest_confidence(parts: Iterable[RangeEstimate]) -> str:
    levels = [_CONF_ORDER.get(p.confidence, 1) for p in parts]
    return _CONF_NAME[min(levels)] if levels else "low"


def money(
    lo: float, hi: Optional[float] = None, *,
    provenance: Provenance = Provenance.DERIVED,
    confidence: str = "medium", source: str = "",
) -> RangeEstimate:
    """Build a ranged monetary/numeric value. A single figure becomes min==max
    — a point value, not a fabricated spread."""
    return RangeEstimate(min=lo, max=(lo if hi is None else hi),
                         confidence=confidence, provenance=provenance, source=source)


def midpoint(r: RangeEstimate) -> float:
    return (r.min + r.max) / 2.0


def add(*parts: RangeEstimate, source: str = "") -> RangeEstimate:
    parts = tuple(p for p in parts if p is not None)
    if not parts:
        return money(0.0, source="sum of no components")
    return RangeEstimate(
        min=sum(p.min for p in parts), max=sum(p.max for p in parts),
        confidence=weakest_confidence(parts), provenance=Provenance.DERIVED,
        source=source or f"sum of {len(parts)} components",
    )


def sub(a: RangeEstimate, b: RangeEstimate, *, source: str = "") -> RangeEstimate:
    """Interval subtraction: widest-case (a.min - b.max, a.max - b.min).

    Note this can straddle zero even when both inputs are comfortably positive —
    which is the honest result when two uncertain quantities are differenced,
    and is precisely why a savings figure may legitimately span negative values.
    """
    return RangeEstimate(
        min=a.min - b.max, max=a.max - b.min,
        confidence=weakest_confidence([a, b]), provenance=Provenance.DERIVED,
        source=source or "difference of two ranges (widest case)",
    )


def mul(a: RangeEstimate, b: RangeEstimate, *, source: str = "") -> RangeEstimate:
    products = [a.min * b.min, a.min * b.max, a.max * b.min, a.max * b.max]
    return RangeEstimate(
        min=min(products), max=max(products),
        confidence=weakest_confidence([a, b]), provenance=Provenance.DERIVED,
        source=source or "product of two ranges",
    )


def scale(a: RangeEstimate, k: float, *, source: str = "") -> RangeEstimate:
    lo, hi = sorted((a.min * k, a.max * k))
    return RangeEstimate(
        min=lo, max=hi, confidence=a.confidence, provenance=Provenance.DERIVED,
        source=source or f"scaled by {k}",
    )


def div(a: RangeEstimate, b: RangeEstimate, *, source: str = "") -> Optional[RangeEstimate]:
    """Interval division. Returns None when the divisor spans zero — an
    undefined result is reported as undefined, never as a large number."""
    if b.min <= 0 <= b.max:
        return None
    quotients = [a.min / b.min, a.min / b.max, a.max / b.min, a.max / b.max]
    return RangeEstimate(
        min=min(quotients), max=max(quotients),
        confidence=weakest_confidence([a, b]), provenance=Provenance.DERIVED,
        source=source or "quotient of two ranges",
    )


def complement(a: RangeEstimate, *, source: str = "") -> RangeEstimate:
    """1 - a, for fractions expressed 0-1."""
    return RangeEstimate(
        min=1.0 - a.max, max=1.0 - a.min, confidence=a.confidence,
        provenance=Provenance.DERIVED, source=source or "1 - fraction",
    )


class LineStatus(str, Enum):
    """Spec 8.2: an unknown component is reported as ABSENT, never as zero.

    An absent line is excluded from totals and listed explicitly, so a total
    can never quietly understate cost by treating "we did not ask" as "zero".
    """
    KNOWN = "known"
    ABSENT = "absent"


class CostLine(BaseModel):
    key: str
    label: str
    amount: Optional[RangeEstimate] = None
    status: LineStatus = LineStatus.KNOWN
    note: str = ""

    @classmethod
    def absent(cls, key: str, label: str, note: str) -> "CostLine":
        return cls(key=key, label=label, amount=None,
                   status=LineStatus.ABSENT, note=note)


class CostBreakdown(BaseModel):
    """A set of cost lines plus the total of the KNOWN ones."""
    label: str
    lines: list[CostLine] = Field(default_factory=list)

    @property
    def known_lines(self) -> list[CostLine]:
        return [l for l in self.lines
                if l.status == LineStatus.KNOWN and l.amount is not None]

    @property
    def absent_lines(self) -> list[CostLine]:
        return [l for l in self.lines if l.status == LineStatus.ABSENT]

    def total(self) -> RangeEstimate:
        parts = [l.amount for l in self.known_lines]
        return add(*parts, source=f"{self.label}: sum of {len(parts)} known components")

    def completeness_note(self) -> str:
        missing = self.absent_lines
        if not missing:
            return "all components accounted for"
        return ("excludes " + ", ".join(l.label.lower() for l in missing) +
                " — not collected, so the total is a floor, not a complete figure")


# ---------------------------------------------------------------------------
# Scoring types (spec 9)
# ---------------------------------------------------------------------------

class SubScore(BaseModel):
    """One weighted factor inside a score."""
    key: str
    label: str
    value: float                  # 0-100
    weight: float
    basis: str                    # the input value, in words
    provenance: Provenance = Provenance.DERIVED
    note: str = ""

    @property
    def contribution(self) -> float:
        return self.value * self.weight


class BoundsType(str, Enum):
    """S4: what a score's bounds actually represent.

    A band that varies only the numeric inputs while holding categorical ones
    fixed looks more certain than the assessment is. The type says which.
    """
    NUMERIC_INPUT_ENVELOPE = "numeric_input_envelope"
    SCENARIO_ENVELOPE = "scenario_envelope"
    UNAVAILABLE = "unavailable"


class Score(BaseModel):
    """A 0-100 analytical indicator (spec 9).

    `computable` is first-class: a score whose inputs are missing is reported
    as not computable with the missing inputs named, never as zero and never
    with terms silently dropped. A zero score and an unknown score mean
    completely different things to a reader.
    """
    key: str
    label: str
    computable: bool = True
    value: Optional[float] = None          # headline, from input midpoints
    bounds: Optional[RangeEstimate] = None  # 0-100, from input bounds
    band: str = ""
    sub_scores: list[SubScore] = Field(default_factory=list)
    missing_inputs: list[str] = Field(default_factory=list)
    flags: list[str] = Field(default_factory=list)
    note: str = ""
    # S4: bounds transparency.
    bounds_type: BoundsType = BoundsType.UNAVAILABLE
    inputs_varied: list[str] = Field(default_factory=list)
    inputs_held_fixed: list[str] = Field(default_factory=list)
    calibration_version: int = 1

    @classmethod
    def not_computable(cls, key: str, label: str, missing: list[str]) -> "Score":
        return cls(key=key, label=label, computable=False, band="not computable",
                   missing_inputs=missing,
                   note=("cannot be computed — missing: " + ", ".join(missing) +
                         ". Reported as unknown, not as zero."))


BANDS = ((80, "high"), (60, "moderate-high"), (40, "moderate"), (20, "low"), (0, "very low"))


def band_for(value: float) -> str:
    for threshold, name in BANDS:
        if value >= threshold:
            return name
    return "very low"


def clamp(value: float, lo: float = 0.0, hi: float = 100.0) -> float:
    return max(lo, min(hi, value))
