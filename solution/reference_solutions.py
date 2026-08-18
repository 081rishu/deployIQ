"""Curated reference/benchmark solutions for the two sectors.

Reference solutions provide a maintained baseline architecture to compare the
estimated solution against (spec 7.1).
"""

from __future__ import annotations

from schemas.assessment_state import Sector
from solution.schema import Capability, ReferenceSolution


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
            "Very high volume (>50k/mo) may justify custom service for scale",
            "Compliance-heavy environment may require on-prem deployment",
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
            "Highly variable document layouts may need fine-tuned extraction model",
            "Low data quality raises human-review rate",
        ],
    ),
]


def for_sector(sector: Sector) -> list[ReferenceSolution]:
    return [r for r in REFERENCES if sector in r.sectors]
