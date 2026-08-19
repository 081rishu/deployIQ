"""Report-layer evidence index — spec 13.3.  [PRESENTATION LAYER]

WHY THIS EXISTS
---------------
`calc/models.py` combines ranges with interval arithmetic, and every combining
operation (`add`, `sub`, `mul`, `div`, `scale`) builds a fresh `RangeEstimate`
tagged DERIVED with a prose `source` and **no `source_id`**. That is correct for
the engine — a sum of two figures has no single evidence id — but it means the
evidence identity of every headline number is gone by the time the report sees
it, and spec 13.3 requires the report to disclose figures verified below
`primary_document`.

So the report rebuilds identity from the LEAVES instead of asking the frozen
layer to carry it: it walks the consumed objects, collects every `source_id`
that is still attached to an un-combined value, and lets a derived figure cite
its INPUT SET. `calc/models.py` is not reopened.

RULES
-----
  * An evidence id is never invented. The index is built FROM the registries,
    so an id resolves only if a registry actually holds it.
  * An id that resolves nowhere is SURFACED, never dropped and never turned
    into a plausible-looking Citation.
  * `verification` stays separate from `provenance`, and a registry that
    records no verification tier yields None rather than a flattering default.
  * Currency has exactly ONE source: `AssessmentState.currency`. This module
    reads it; it does not compute a second opinion.

KNOWN UPSTREAM QUIRK
--------------------
`calc.driver_ranking.DriverImpact.evidence_ids` does NOT contain evidence ids.
It is populated from `TaskAutomationEstimate.benchmark_anchor`, which holds a
rendered citation STRING (solution/estimator.py builds it from
`Anchor.citation`, not `Anchor.evidence_id`). Those values are excluded from id
collection and carried as prose, so the report neither resolves them nor
reports them as unresolved evidence. Recorded rather than worked around,
because the frozen layers stay frozen.
"""

from __future__ import annotations

import re
from functools import lru_cache
from typing import Any, Iterable, Optional

from pydantic import BaseModel, ConfigDict, Field

from report.schema import Citation, EvidenceRegistry, Figure
from schemas.assessment_state import AssessmentState, Provenance, RangeEstimate, Sector

# ---------------------------------------------------------------------------
# Collection: which fields actually carry an evidence id
# ---------------------------------------------------------------------------

# Scalar id fields on any model.
_ID_FIELDS_SCALAR = frozenset({"source_id", "evidence_id"})
# List-of-id fields.
_ID_FIELDS_LIST = frozenset({"source_ids", "evidence_ids", "inference_pricing_ids",
                             "pricing_ids"})
# (class name, field name) pairs whose contents are NOT ids despite the name.
_NOT_IDS = frozenset({("DriverImpact", "evidence_ids"),
                      ("DriverVariable", "evidence_ids"),
                      ("DriverEntry", "evidence_notes")})


def collect_source_ids(*objects: Any) -> list[str]:
    """Walk objects and return every evidence id still attached to a leaf.

    Order-preserving and de-duplicated: an id contributed by three different
    leaves is one id, not three, so a figure cannot cite the same source twice
    (spec 13.3).
    """
    found: list[str] = []
    seen: set[str] = set()
    visited: set[int] = set()

    def add(value: Any) -> None:
        if isinstance(value, str) and value.strip() and value not in seen:
            seen.add(value)
            found.append(value)

    def walk(obj: Any) -> None:
        if obj is None or isinstance(obj, (str, int, float, bool)):
            return
        marker = id(obj)
        if marker in visited:
            return
        visited.add(marker)

        if isinstance(obj, BaseModel):
            cls_name = type(obj).__name__
            for name in type(obj).model_fields:
                if (cls_name, name) in _NOT_IDS:
                    continue
                value = getattr(obj, name, None)
                if name in _ID_FIELDS_SCALAR:
                    add(value)
                elif name in _ID_FIELDS_LIST and isinstance(value, (list, tuple)):
                    for item in value:
                        add(item)
                else:
                    walk(value)
            return

        if isinstance(obj, dict):
            for key, value in obj.items():
                if key in _ID_FIELDS_SCALAR:
                    add(value)
                elif key in _ID_FIELDS_LIST and isinstance(value, (list, tuple)):
                    for item in value:
                        add(item)
                else:
                    walk(value)
            return

        if isinstance(obj, (list, tuple, set)):
            for item in obj:
                walk(item)

    for obj in objects:
        walk(obj)
    return found


# Calibration parameters do NOT attach a `source_id` to the ranges they
# produce; `calc/calibration.py` instead formats its own id into the citation
# string as "calibration v1 [review_fraction.human_review], reviewed ...". That
# bracketed token is the registry's own convention, written by the registry
# itself, so reading it back is resolution rather than guesswork — but ONLY ids
# the registry actually holds are accepted, so a stray bracket in prose can
# never become a citation.
_BRACKETED = re.compile(r"\[([A-Za-z0-9_.\-]+)\]")

# Fields whose prose may carry a bracketed calibration id.
_TEXT_FIELDS = frozenset({"source", "derivation", "statement", "basis", "note",
                          "rationale", "verdict", "primary_basis"})


def collect_text_declared_ids(*objects: Any) -> list[str]:
    """Return bracketed ids found in citation/derivation prose, de-duplicated.

    Callers MUST filter these against the index before use — this function only
    reports candidates, and a candidate is not evidence.
    """
    found: list[str] = []
    seen: set[str] = set()
    visited: set[int] = set()

    def walk(obj: Any) -> None:
        if obj is None or isinstance(obj, (int, float, bool)):
            return
        if isinstance(obj, str):
            return
        marker = id(obj)
        if marker in visited:
            return
        visited.add(marker)

        if isinstance(obj, BaseModel):
            for name in type(obj).model_fields:
                value = getattr(obj, name, None)
                if name in _TEXT_FIELDS and isinstance(value, str):
                    for token in _BRACKETED.findall(value):
                        if token not in seen:
                            seen.add(token)
                            found.append(token)
                else:
                    walk(value)
            return
        if isinstance(obj, dict):
            for key, value in obj.items():
                if key in _TEXT_FIELDS and isinstance(value, str):
                    for token in _BRACKETED.findall(value):
                        if token not in seen:
                            seen.add(token)
                            found.append(token)
                else:
                    walk(value)
            return
        if isinstance(obj, (list, tuple, set)):
            for item in obj:
                walk(item)

    for obj in objects:
        walk(obj)
    return found


# ---------------------------------------------------------------------------
# The index
# ---------------------------------------------------------------------------

class EvidenceResolution(BaseModel):
    """The result of resolving a set of ids: what was found, and what was not."""
    model_config = ConfigDict(frozen=True)

    source_ids: list[str] = Field(default_factory=list)
    citations: list[Citation] = Field(default_factory=list)
    unresolved: list[str] = Field(default_factory=list)
    ambiguous: list[str] = Field(default_factory=list)

    @property
    def below_primary(self) -> list[Citation]:
        return [c for c in self.citations if c.below_primary]

    @property
    def provenance_mix(self) -> list[Provenance]:
        mix: list[Provenance] = []
        for c in self.citations:
            if c.provenance is not None and c.provenance not in mix:
                mix.append(c.provenance)
        return mix


class EvidenceIndex(BaseModel):
    """Every evidence id the system can legitimately cite, resolved once."""
    model_config = ConfigDict(frozen=True)

    citations: dict[str, Citation] = Field(default_factory=dict)
    # Ids claimed by more than one registry. Never silently resolved to the
    # first winner — an ambiguous id is surfaced like an unresolved one.
    ambiguous: list[str] = Field(default_factory=list)
    registry_counts: dict[str, int] = Field(default_factory=dict)

    def resolve(self, evidence_id: str) -> Optional[Citation]:
        if evidence_id in self.ambiguous:
            return None
        return self.citations.get(evidence_id)

    def resolve_many(self, ids: Iterable[str]) -> EvidenceResolution:
        source_ids: list[str] = []
        citations: list[Citation] = []
        unresolved: list[str] = []
        ambiguous: list[str] = []
        seen: set[str] = set()
        for raw in ids:
            if not raw or raw in seen:
                continue
            seen.add(raw)
            source_ids.append(raw)
            if raw in self.ambiguous:
                ambiguous.append(raw)
                continue
            found = self.citations.get(raw)
            if found is None:
                unresolved.append(raw)
            else:
                citations.append(found)
        return EvidenceResolution(source_ids=source_ids, citations=citations,
                                  unresolved=unresolved, ambiguous=ambiguous)

    def resolve_objects(self, *objects: Any, include_calibration: bool = True
                        ) -> EvidenceResolution:
        """Collect ids from live pipeline objects and resolve them in one step.

        `include_calibration` also picks up calibration ids that the calibration
        registry wrote into its own citation strings, keeping only those the
        index actually holds. Without it, the assumptions behind a derived
        figure are invisible to the report even though they moved the number.
        """
        ids = list(collect_source_ids(*objects))
        if include_calibration:
            known = set(self.citations) - set(self.ambiguous)
            ids += [candidate for candidate in collect_text_declared_ids(*objects)
                    if candidate in known and candidate not in ids]
        return self.resolve_many(ids)

    def decorate(self, figure: Figure) -> Figure:
        """Return a copy of `figure` with its citations resolved.

        Provenance is NOT inferred from the citations: a figure's own tag is
        left exactly as the upstream value set it. Only the citation list, the
        unresolved list and the provenance MIX are filled in.
        """
        resolution = self.resolve_many(figure.source_ids)
        return figure.model_copy(update={
            "citations": resolution.citations,
            "unresolved_source_ids": resolution.unresolved + resolution.ambiguous,
            "provenance_mix": (figure.provenance_mix
                               or resolution.provenance_mix),
        })


def _benchmark_citations() -> list[Citation]:
    from lib.benchmarks import load_pack
    out: list[Citation] = []
    for sector in Sector:
        for fig in load_pack(sector).figures:
            out.append(Citation(
                evidence_id=fig.evidence_id, source=fig.source,
                source_url=fig.source_url or None, as_of=fig.as_of,
                geography=fig.geography, verification=fig.verification,
                provenance=(Provenance.SOURCED if fig.provenance == "sourced"
                            else Provenance.ASSUMED),
                registry=EvidenceRegistry.BENCHMARK,
                note=f"{sector.value} pack: {fig.label}"))
    return out


def _pricing_citations() -> list[Citation]:
    from lib.pricing import load_pricing
    out: list[Citation] = []
    for rec in load_pricing().records:
        out.append(Citation(
            evidence_id=rec.pricing_id, source=rec.citation(),
            source_url=rec.source_url or None, as_of=rec.effective_date,
            geography=rec.geography,
            # The pricing registry records no verification tier. None means
            # "not recorded", which the report treats as below primary.
            verification=None,
            provenance=(Provenance.SOURCED if rec.provenance == "sourced"
                        else Provenance.ASSUMED),
            registry=EvidenceRegistry.PRICING,
            note=(f"{rec.provider} {rec.service}, {rec.unit}, {rec.currency}; "
                  f"the pricing registry records no verification tier")))
    return out


def _labor_rate_citations() -> list[Citation]:
    from lib.labor_rates import load_rates
    out: list[Citation] = []
    for entry in load_rates().entries:
        out.append(Citation(
            evidence_id=entry.rate_id, source=entry.source,
            source_url=entry.source_url or None, as_of=entry.retrieved,
            geography=entry.geography, verification=entry.verification,
            provenance=(Provenance.SOURCED if entry.provenance == "sourced"
                        else Provenance.ASSUMED),
            registry=EvidenceRegistry.LABOR_RATE,
            note=f"{entry.role} ({entry.labor_kind.value}), {entry.currency}"))
    return out


def _compliance_citations() -> list[Citation]:
    from lib.compliance import load_attestations
    out: list[Citation] = []
    for att in load_attestations():
        # An attestation with no retrievable source cannot be called `sourced`
        # (spec 4.3's guardrail). Provenance is left UNKNOWN rather than
        # guessed, and the gap is visible in the citation itself.
        provenance = Provenance.SOURCED if att.source_url else None
        out.append(Citation(
            evidence_id=att.evidence_id,
            source=(att.source_name or att.title or att.attestation_type
                    or "compliance attestation"),
            source_url=att.source_url or None, as_of=(att.retrieved_at or ""),
            geography="", verification=None, provenance=provenance,
            registry=EvidenceRegistry.COMPLIANCE,
            note=(f"{att.standard}: {att.claim_status.value}"
                  + ("" if att.source_url else
                     "; no retrievable source recorded, so provenance is left "
                     "unknown rather than claimed")),
        ))
    return out


def _calibration_citations() -> list[Citation]:
    """Calibration parameters are citable, and they are assumptions.

    Ids come from each registry's own key — nothing is minted here. These are
    versioned product calibrations with stated rationales, not empirical
    industry data, and the `assumed` tag says so.
    """
    from calc import calibration as econ_cal
    from calc import scoring_calibration as score_cal
    from solution import calibration as scope_cal

    out: list[Citation] = []
    for row in econ_cal.audit_table():
        out.append(Citation(
            evidence_id=str(row["calibration_id"]),
            source=f"economic calibration v{row.get('version')}: {row.get('rationale','')}",
            as_of=str(row.get("last_reviewed", "")), verification=None,
            provenance=Provenance.ASSUMED, registry=EvidenceRegistry.CALIBRATION,
            note=f"unit: {row.get('unit', '')}"))
    for row in score_cal.audit_table():
        out.append(Citation(
            evidence_id=str(row["parameter_id"]),
            source=f"scoring calibration v{row.get('version')}: {row.get('rationale','')}",
            as_of=str(row.get("last_reviewed", "")), verification=None,
            provenance=Provenance.ASSUMED, registry=EvidenceRegistry.CALIBRATION,
            note=f"unit: {row.get('unit', '')}"))
    for param in scope_cal.all_calibration_params():
        out.append(Citation(
            evidence_id=str(param.key),
            source=f"scope calibration v{param.version}: {param.rationale}",
            as_of=str(param.last_reviewed or ""), verification=None,
            provenance=Provenance.ASSUMED, registry=EvidenceRegistry.CALIBRATION,
            note=f"unit: {param.unit}"))
    return out


@lru_cache(maxsize=1)
def build_index() -> EvidenceIndex:
    """Load every registry once and index it by its own ids."""
    citations: dict[str, Citation] = {}
    ambiguous: list[str] = []
    counts: dict[str, int] = {}

    for group in (_benchmark_citations(), _pricing_citations(),
                  _labor_rate_citations(), _compliance_citations(),
                  _calibration_citations()):
        for citation in group:
            counts[citation.registry.value] = counts.get(citation.registry.value, 0) + 1
            existing = citations.get(citation.evidence_id)
            if existing is None:
                citations[citation.evidence_id] = citation
            elif existing.registry != citation.registry:
                # Two registries claim the same id. Resolving it either way
                # would attribute a figure to a source that may not be its own.
                if citation.evidence_id not in ambiguous:
                    ambiguous.append(citation.evidence_id)
    return EvidenceIndex(citations=citations, ambiguous=ambiguous,
                         registry_counts=counts)


# ---------------------------------------------------------------------------
# Currency
# ---------------------------------------------------------------------------

class CurrencyResolution(BaseModel):
    """Currency for the whole report, from the ONE authoritative source.

    `AssessmentState.currency` derives from geography. When it is None the
    report says so and renders unitless figures with a declared gap; it never
    picks a symbol, and never borrows one from a labor-rate or pricing record
    (those describe the source's currency, not the assessment's).
    """
    model_config = ConfigDict(frozen=True)

    currency: Optional[str] = None
    basis: str
    resolved: bool = False


def resolve_currency(state: AssessmentState) -> CurrencyResolution:
    currency = state.currency
    if currency:
        return CurrencyResolution(
            currency=currency, resolved=True,
            basis=(f"derived from geography {state.geography!r} "
                   f"(AssessmentState.currency)"))
    if state.geography:
        return CurrencyResolution(
            currency=None, resolved=False,
            basis=(f"geography {state.geography!r} maps to no known currency, so "
                   f"no currency could be established"))
    return CurrencyResolution(
        currency=None, resolved=False,
        basis="no geography on the assessment, so no currency could be established")
