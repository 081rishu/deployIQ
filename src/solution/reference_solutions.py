"""Curated reference/benchmark solutions for the two sectors.

A reference solution is the maintained baseline architecture for a sector
(spec 7.1). It is not a hardcoded answer: each one carries the conditions
under which departing from it is legitimate, and the ranker evaluates those
conditions against the assessment rather than always anchoring to the
baseline. See solution/ranking.py for how alignment is scored.
"""

from __future__ import annotations

from schemas.assessment_state import Sector
from solution.schema import (
    Capability,
    ImplementationKind,
    DeviationCondition,
    DeviationTrigger,
    ReferenceSolution,
)


REFERENCES: list[ReferenceSolution] = [
    ReferenceSolution(
        id="cs_baseline",
        sectors=[Sector.CUSTOMER_SUPPORT],
        pattern="ai_assisted_workflow",
        expected_capabilities=[
            Capability.INGEST, Capability.CLASSIFY, Capability.GENERATE,
            Capability.ROUTE, Capability.HUMAN_ESCALATE,
        ],
        recommended_architecture=(
            "Ticket ingest -> intent classify (LLM) -> auto-draft reply -> "
            "route complex/compliance cases to human review"
        ),
        rationale=(
            "Balances automation of routine tickets against human escalation "
            "for high-risk/complex cases; low-code enough for fast delivery."
        ),
        conditions_for_deviation=[
            DeviationCondition(
                id="cs_high_volume",
                description="Very high volume (>50k/mo) may justify a custom service for scale",
                trigger=DeviationTrigger.MONTHLY_VOLUME_ABOVE,
                threshold=50000,
                releases_to_kinds=[ImplementationKind.CUSTOM_CODE.value,
                                   ImplementationKind.MANAGED_SERVICE.value],
            ),
            DeviationCondition(
                id="cs_compliance_on_prem",
                description="Compliance-heavy environment may require on-prem deployment",
                trigger=DeviationTrigger.COMPLIANCE_PRESENT,
                releases_to_kinds=[ImplementationKind.CUSTOM_CODE.value],
            ),
        ],
    ),
    ReferenceSolution(
        id="doc_baseline",
        sectors=[Sector.DOCUMENT_PROCESSING],
        pattern="document_pipeline",
        expected_capabilities=[
            Capability.INGEST, Capability.EXTRACT, Capability.CLASSIFY,
            Capability.VALIDATE, Capability.HUMAN_REVIEW,
        ],
        recommended_architecture=(
            "Invoice ingest -> OCR/extract -> classify -> validate against rules "
            "-> human review for low-confidence exceptions"
        ),
        rationale=(
            "Automates high-volume extraction with confidence-based human "
            "review; matches straight-through-processing benchmark pattern."
        ),
        conditions_for_deviation=[
            DeviationCondition(
                id="doc_high_volume",
                description="Very high volume (>50k/mo) may justify a custom service for scale",
                trigger=DeviationTrigger.MONTHLY_VOLUME_ABOVE,
                threshold=50000,
                releases_to_kinds=[ImplementationKind.CUSTOM_CODE.value,
                                   ImplementationKind.MANAGED_SERVICE.value],
            ),
            DeviationCondition(
                id="doc_variable_layouts",
                description=(
                    "Highly variable document layouts may need a fine-tuned "
                    "extraction model"
                ),
                # Layout variability is not captured anywhere in AssessmentState,
                # so this cannot be evaluated deterministically — surfaced for a
                # human rather than guessed at.
                trigger=DeviationTrigger.MANUAL,
            ),
            DeviationCondition(
                id="doc_low_data_quality",
                description="Low data quality raises the human-review rate",
                trigger=DeviationTrigger.MANUAL,
            ),
        ],
    ),
]


def for_sector(sector: Sector) -> list[ReferenceSolution]:
    return [r for r in REFERENCES if sector in r.sectors]


def reference_for(sector: Sector) -> ReferenceSolution | None:
    """The single baseline for a sector (the first registered)."""
    refs = for_sector(sector)
    return refs[0] if refs else None
