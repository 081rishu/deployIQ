"""Evidence and benchmark integrity — C1 and C4.

C1: the LLM must never be able to invent a citation and have it stored as
authoritative evidence. Two mechanisms:

  * `anchor_automation` / `metric_benchmark` are the ONLY paths that produce a
    `sourced` value, and both read from the curated benchmark packs.
  * `enforce_provenance` sweeps a finished estimate and downgrades any value
    claiming `sourced` whose citation does not match a real pack entry.

Provenance vocabulary note: the fixes document lists
benchmark/evidence/llm_estimate/assumption/derived, but that is the parallel
set spec 6 replaced (and which the same document forbids re-introducing in
its own C10/C11 sections). The canonical five are used throughout:

    benchmark, evidence -> sourced      llm_estimate -> estimated
    assumption          -> assumed      derived      -> derived

C4: an LLM automation estimate is compared against the sector's achieved-today
benchmark where one exists. The benchmark is CONTEXT, never the company's
expected result — so it never overwrites the estimate. It records the anchor,
flags material divergence and lowers confidence when the model claims far more
than the industry currently achieves.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Optional

from pydantic import BaseModel

from lib.benchmarks import figure as benchmark_figure
from lib.benchmarks import load_pack
from schemas.assessment_state import Provenance, RangeEstimate, Sector
from solution.schema import Capability

# Capability -> the pack figure describing what the industry achieves today.
_CAPABILITY_ANCHORS = {
    Sector.DOCUMENT_PROCESSING: {
        Capability.EXTRACT: "straight_through_processing_rate",
        Capability.VALIDATE: "straight_through_processing_rate",
    },
    Sector.CUSTOMER_SUPPORT: {},   # no sourced automation benchmark yet
}

# Performance metric -> pack figure (shared with solution/performance.py).
_METRIC_ANCHORS = {
    "stp_rate": "straight_through_processing_rate",
    "exception_rate": "invoice_exception_rate",
}

# An LLM claim this far above the industry anchor is flagged.
DIVERGENCE_FLAG_THRESHOLD = 0.25     # 25 percentage points


class Anchor(BaseModel):
    figure_key: str
    evidence_id: str
    citation: str
    applicability: str = ""
    limitations: str = ""
    min: float
    max: float


class AnchoredEstimate(BaseModel):
    estimate: RangeEstimate
    anchor: Optional[Anchor] = None
    divergence_note: str = ""


@lru_cache(maxsize=1)
def _valid_evidence_ids() -> frozenset[str]:
    """Every evidence_id a `sourced` value is allowed to reference (N8).

    Matching on a stable ID rather than a rendered citation means reformatting
    a source's text cannot invalidate a legitimate evidence relationship, and
    inventing plausible-looking citation prose cannot create one.
    """
    out = set()
    for sector in Sector:
        try:
            pack = load_pack(sector)
        except FileNotFoundError:
            continue
        for fig in pack.figures:
            if fig.provenance == "sourced":
                out.add(fig.evidence_id)
    return frozenset(out)


def metric_benchmark(sector: Sector, metric: str) -> Optional[RangeEstimate]:
    """A sourced figure for a performance metric, or None."""
    key = _METRIC_ANCHORS.get(metric)
    if not key:
        return None
    fig = benchmark_figure(sector, key)
    if fig is None or fig.provenance != "sourced":
        return None
    return fig.as_range()


def anchor_automation(
    sector: Sector, capability: Capability, llm_range: RangeEstimate,
) -> AnchoredEstimate:
    """C4: compare an LLM automation estimate against industry evidence.

    The estimate is NOT replaced. A benchmark describes what the industry
    achieves today, which is neither a ceiling on what this company could
    achieve nor a promise that it will.
    """
    key = _CAPABILITY_ANCHORS.get(sector, {}).get(capability)
    fig = benchmark_figure(sector, key) if key else None
    if fig is None or fig.provenance != "sourced":
        return AnchoredEstimate(
            estimate=RangeEstimate(
                min=llm_range.min, max=llm_range.max, confidence=llm_range.confidence,
                provenance=Provenance.ESTIMATED,
                source="llm_estimate (no applicable benchmark for this capability)"),
            divergence_note="")

    lo, hi = fig.bounds
    anchor = Anchor(figure_key=key, evidence_id=fig.evidence_id,
                    citation=fig.citation(), applicability=fig.applicability,
                    limitations=fig.limitations, min=lo, max=hi)
    midpoint_claim = (llm_range.min + llm_range.max) / 2.0
    gap = midpoint_claim - ((lo + hi) / 2.0)

    note, confidence = "", llm_range.confidence
    if gap > DIVERGENCE_FLAG_THRESHOLD * 100:
        note = (f"claimed automation ({llm_range.min:.0f}-{llm_range.max:.0f}%) is "
                f"{gap:.0f} points above the industry benchmark of {lo:.1f}% "
                f"({fig.citation()}). The benchmark is what the industry achieves "
                f"today, not a ceiling — but a gap this size needs a reason.")
        confidence = "low"

    return AnchoredEstimate(
        estimate=RangeEstimate(
            min=llm_range.min, max=llm_range.max, confidence=confidence,
            provenance=Provenance.ESTIMATED,
            source=(f"llm_estimate, anchored against {fig.citation()} "
                    f"(industry {lo:.1f}-{hi:.1f}%)")),
        anchor=anchor, divergence_note=note)


def enforce_provenance(value: RangeEstimate, where: str) -> tuple[RangeEstimate, Optional[str]]:
    """C1: a value may only claim `sourced` if its citation is a real pack entry.

    Anything else claiming `sourced` is downgraded to `estimated` and reported.
    This is the backstop that makes a fabricated citation structurally unable
    to reach the report as evidence.
    """
    if value.provenance != Provenance.SOURCED:
        return value, None
    if value.source_id and value.source_id in _valid_evidence_ids():
        return value, None
    reason = ("no evidence_id" if not value.source_id
              else f"evidence_id {value.source_id!r} is not in the registry")
    return (RangeEstimate(
        min=value.min, max=value.max, confidence="low",
        provenance=Provenance.ESTIMATED, source_id=None,
        source=f"downgraded from 'sourced': {reason} "
               f"(original text: {value.source[:70]!r})"),
        f"{where}: unbacked 'sourced' claim downgraded to 'estimated' ({reason})")
