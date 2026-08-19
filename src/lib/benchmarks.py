"""Static sector benchmark packs (spec 4, ARCHITECTURE.txt 3.7).

Loads `data/sector_benchmarks/{sector}.json` at request time — no live calls.
Figures are exposed as the canonical `RangeEstimate`, so a benchmark value
enters the rest of the system carrying its provenance tag and citation, the
same as any other number (spec 6).

Guardrail: a figure may only claim `sourced` if it names a retrievable source
with a URL and a date. Anything whose chain of custody stops at a secondary
summary must be tagged `assumed` — spec 4.3 exists to stop an industry average
being laundered into something that reads as independently sourced.
"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Literal, Optional

from pydantic import BaseModel, Field, model_validator

from core.paths import data_path
from schemas.assessment_state import Provenance, RangeEstimate, Sector

_PACK_DIR = data_path("sector_benchmarks")

# How firmly the figure was checked, independent of its provenance tag:
#   primary_document — the source document/page itself was retrieved and read
#   search_snippet   — attributed to a named primary source, but read from a
#                      search result because the source blocks retrieval
#   unverified       — no primary source located
Verification = Literal["primary_document", "search_snippet", "unverified"]


class BenchmarkFigure(BaseModel):
    # Stable identifier. Provenance validation matches on this, never on the
    # rendered citation string, so reformatting a source cannot invalidate a
    # legitimate evidence relationship (N8).
    evidence_id: str
    key: str
    label: str
    unit: str
    provenance: Literal["sourced", "assumed"]
    verification: Verification
    source: str
    as_of: str
    geography: str
    notes: str = ""
    source_url: Optional[str] = None
    # Context that decides whether a figure may anchor an estimate at all (N7).
    population: str = ""
    applicability: str = ""
    limitations: str = ""
    value: Optional[float] = None
    min: Optional[float] = None
    max: Optional[float] = None

    @model_validator(mode="after")
    def _check(self) -> "BenchmarkFigure":
        if self.value is None and (self.min is None or self.max is None):
            raise ValueError(f"{self.key}: needs either `value` or both `min` and `max`")
        if self.min is not None and self.max is not None and self.min > self.max:
            raise ValueError(f"{self.key}: min {self.min} > max {self.max}")
        if self.provenance == "sourced":
            if not self.source_url:
                raise ValueError(f"{self.key}: `sourced` requires a source_url")
            if self.verification == "unverified":
                raise ValueError(
                    f"{self.key}: cannot claim `sourced` with verification "
                    f"`unverified` — tag it `assumed` instead (spec 4.3)"
                )
        return self

    @property
    def bounds(self) -> tuple[float, float]:
        if self.value is not None:
            return float(self.value), float(self.value)
        return float(self.min), float(self.max)  # type: ignore[arg-type]

    def citation(self) -> str:
        parts = [self.source]
        if self.as_of:
            parts.append(f"as of {self.as_of}")
        if self.geography:
            parts.append(self.geography)
        return " — ".join(parts)

    def as_range(self) -> RangeEstimate:
        """Expose the figure as the canonical ranged value, provenance intact.

        A single-value figure becomes min == max: it is a point benchmark, not
        a fabricated spread.
        """
        lo, hi = self.bounds
        confidence = {
            "primary_document": "high",
            "search_snippet": "medium",
            "unverified": "low",
        }[self.verification]
        return RangeEstimate(
            min=lo, max=hi, confidence=confidence,
            provenance=(Provenance.SOURCED if self.provenance == "sourced"
                        else Provenance.ASSUMED),
            source=self.citation(), source_id=self.evidence_id,
        )


class BenchmarkPack(BaseModel):
    sector: Sector
    pack_version: str
    description: str = ""
    figures: list[BenchmarkFigure] = Field(default_factory=list)

    @model_validator(mode="after")
    def _unique_keys(self) -> "BenchmarkPack":
        keys = [f.key for f in self.figures]
        dupes = {k for k in keys if keys.count(k) > 1}
        if dupes:
            raise ValueError(f"duplicate figure keys in {self.sector.value}: {sorted(dupes)}")
        return self

    def get(self, key: str) -> Optional[BenchmarkFigure]:
        return next((f for f in self.figures if f.key == key), None)

    def by_evidence_id(self, evidence_id: str) -> Optional[BenchmarkFigure]:
        return next((f for f in self.figures if f.evidence_id == evidence_id), None)

    def health(self) -> dict[str, int]:
        """Counts feeding Overall Assessment Confidence (spec 9.7)."""
        return {
            "figures": len(self.figures),
            "sourced": sum(1 for f in self.figures if f.provenance == "sourced"),
            "assumed": sum(1 for f in self.figures if f.provenance == "assumed"),
            "primary_verified": sum(
                1 for f in self.figures if f.verification == "primary_document"),
        }


@lru_cache(maxsize=None)
def load_pack(sector: Sector) -> BenchmarkPack:
    path = _PACK_DIR / f"{sector.value}.json"
    if not path.exists():
        raise FileNotFoundError(f"no benchmark pack for sector '{sector.value}' at {path}")
    return BenchmarkPack.model_validate(json.loads(path.read_text(encoding="utf-8")))


def figure(sector: Sector, key: str) -> Optional[BenchmarkFigure]:
    return load_pack(sector).get(key)


def benchmark_range(sector: Sector, key: str) -> Optional[RangeEstimate]:
    fig = figure(sector, key)
    return fig.as_range() if fig else None
