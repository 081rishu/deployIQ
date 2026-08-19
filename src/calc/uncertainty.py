"""Uncertainty representation — S2, spec sections 6-9.

The old model expressed every input's uncertainty as one `relative_width`
number, mixing three incompatible things: real estimate ranges, synthetic
width from stepping a five-value enum, and an arbitrary +/-15% on inputs that
had no range at all. The categorical artefact was the largest of the three, so
it won the "biggest uncertainty" callout by construction.

Uncertainty is now TYPED, and a numeric width exists only where the input
genuinely has one:

    NUMERIC_RANGE     a real measured/estimated range -> width is meaningful
    ASSUMPTION_RANGE  numeric, but explicitly an assumption
    CATEGORICAL       a category with a confidence level -> NO fake width
    NONE              a firm value

A category never gets a percentage. "Medium" is not "67% uncertain" merely
because the enum has five values.
"""

from __future__ import annotations

from enum import Enum
from typing import Optional

from pydantic import BaseModel

from schemas.assessment_state import Provenance, RangeEstimate


class UncertaintyType(str, Enum):
    NUMERIC_RANGE = "numeric_range"
    ASSUMPTION_RANGE = "assumption_range"
    CATEGORICAL = "categorical"
    NONE = "none"


class Uncertainty(BaseModel):
    field: str
    uncertainty_type: UncertaintyType
    min: Optional[float] = None
    max: Optional[float] = None
    baseline: Optional[float] = None
    category: Optional[str] = None
    confidence: str = "medium"
    provenance: Provenance = Provenance.ESTIMATED
    source_id: Optional[str] = None
    note: str = ""

    @property
    def relative_width(self) -> Optional[float]:
        """Width relative to the baseline — ONLY for genuinely numeric inputs.

        Returns None for categorical uncertainty. A caller that wants to rank
        by width must handle the None rather than receiving a fabricated number.
        """
        if self.uncertainty_type in (UncertaintyType.CATEGORICAL, UncertaintyType.NONE):
            return None
        if self.min is None or self.max is None:
            return None
        base = self.baseline if self.baseline else (self.min + self.max) / 2.0
        if not base:
            return None
        return abs(self.max - self.min) / abs(base)

    @property
    def is_numeric(self) -> bool:
        return self.uncertainty_type in (UncertaintyType.NUMERIC_RANGE,
                                         UncertaintyType.ASSUMPTION_RANGE)

    def describe(self) -> str:
        if self.uncertainty_type == UncertaintyType.CATEGORICAL:
            return (f"{self.field}: category '{self.category}' held with "
                    f"{self.confidence} confidence (no numeric range — a category "
                    f"is not a percentage)")
        if not self.is_numeric:
            return f"{self.field}: firm value"
        kind = ("assumption" if self.uncertainty_type == UncertaintyType.ASSUMPTION_RANGE
                else "estimated range")
        return (f"{self.field}: {self.min:g}-{self.max:g} ({kind}, "
                f"{self.confidence} confidence)")


def from_range(field: str, rng: RangeEstimate, note: str = "") -> Uncertainty:
    """Classify a RangeEstimate by its own provenance."""
    kind = (UncertaintyType.ASSUMPTION_RANGE if rng.provenance == Provenance.ASSUMED
            else UncertaintyType.NUMERIC_RANGE)
    if rng.min == rng.max:
        kind = UncertaintyType.NONE
    return Uncertainty(
        field=field, uncertainty_type=kind, min=rng.min, max=rng.max,
        baseline=(rng.min + rng.max) / 2.0, confidence=rng.confidence,
        provenance=rng.provenance, source_id=rng.source_id,
        note=note or rng.source)


def categorical(field: str, category: str, confidence: str,
                provenance: Provenance = Provenance.USER_PROVIDED,
                note: str = "") -> Uncertainty:
    """A category with a confidence level — never a synthetic width."""
    return Uncertainty(
        field=field, uncertainty_type=UncertaintyType.CATEGORICAL,
        category=category, confidence=confidence, provenance=provenance,
        note=note or "categorical input; uncertainty is the confidence in the "
                     "category, not a numeric spread")
