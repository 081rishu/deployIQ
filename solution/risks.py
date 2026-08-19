"""Structured risk -> control mapping — C13.

Previously every risk got the same string: "add guardrails + review". Filler
in a product whose entire premise is traceability is worse than an absence.

Controls are attached to a risk CATEGORY and to the architecture, not
generated freely. The LLM may rephrase; it does not choose the controls.
"""

from __future__ import annotations

from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field

from solution.schema import Capability, HitlMode, ImplementationOption


class RiskCategory(str, Enum):
    EXTRACTION_ERROR = "extraction_error"
    CLASSIFICATION_ERROR = "classification_error"
    GENERATION_ERROR = "generation_error"
    RETRIEVAL_GAP = "retrieval_gap"
    INTEGRATION_FAILURE = "integration_failure"
    SCALE_LIMIT = "scale_limit"
    COMPLIANCE_GAP = "compliance_gap"
    UNREVIEWED_AUTONOMY = "unreviewed_autonomy"


class RiskControl(BaseModel):
    category: RiskCategory
    risk: str
    controls: list[str] = Field(default_factory=list)
    trigger: str = ""
    # N11: controls the SELECTED implementation actually offers, taken from the
    # registry's control catalog rather than described generically.
    implementation_controls: list[str] = Field(default_factory=list)
    implementation_id: str = ""


_CONTROLS: dict[RiskCategory, tuple[str, list[str]]] = {
    RiskCategory.EXTRACTION_ERROR: (
        "Incorrect field extraction from a document",
        ["schema validation on every extracted record",
         "per-field confidence threshold",
         "route low-confidence extractions to human review",
         "reconciliation against the source system of record"],
    ),
    RiskCategory.CLASSIFICATION_ERROR: (
        "Misrouted or mislabelled item",
        ["confidence threshold with an explicit unknown class",
         "fallback routing queue for low-confidence cases",
         "periodic sampled audit of classifications"],
    ),
    RiskCategory.GENERATION_ERROR: (
        "Generated response is wrong or inappropriate",
        ["response templates constraining the generated surface",
         "grounding check against the retrieved source",
         "human approval before any customer-visible send"],
    ),
    RiskCategory.RETRIEVAL_GAP: (
        "Answer generated without adequate grounding",
        ["minimum retrieval-score threshold before generating",
         "explicit abstain path when nothing relevant is retrieved",
         "citation of the retrieved passage in the output"],
    ),
    RiskCategory.INTEGRATION_FAILURE: (
        "Integration with an existing system fails or times out",
        ["retry with exponential backoff",
         "idempotency keys so retries cannot double-post",
         "dead-letter queue for failed items",
         "endpoint health monitoring with alerting"],
    ),
    RiskCategory.SCALE_LIMIT: (
        "The chosen implementation cannot carry the assessed volume",
        ["load-test at projected peak before rollout",
         "queue-based backpressure",
         "documented migration path off the low-code platform"],
    ),
    RiskCategory.COMPLIANCE_GAP: (
        "Declared compliance constraints are not covered by the stack",
        ["confirm data residency and processing terms before build",
         "audit logging of every automated decision",
         "documented human accountability for automated actions"],
    ),
    RiskCategory.UNREVIEWED_AUTONOMY: (
        "Autonomous actions take effect with no human check",
        ["staged rollout starting in suggest-only mode",
         "reversible actions with an audit trail",
         "sampled post-hoc review with a defined error budget"],
    ),
}

_CAPABILITY_RISKS = {
    Capability.EXTRACT: RiskCategory.EXTRACTION_ERROR,
    Capability.CLASSIFY: RiskCategory.CLASSIFICATION_ERROR,
    Capability.GENERATE: RiskCategory.GENERATION_ERROR,
    Capability.SEARCH_RETRIEVE: RiskCategory.RETRIEVAL_GAP,
}


def _control(category: RiskCategory, trigger: str) -> RiskControl:
    risk, controls = _CONTROLS[category]
    return RiskControl(category=category, risk=risk, controls=controls, trigger=trigger)


def controls_for(
    capabilities: list[Capability], hitl_modes: list[HitlMode],
    integrations: int, compliance_gap: bool, scale_shortfall: bool,
    implementation: Optional[ImplementationOption] = None,
) -> list[RiskControl]:
    """Risks implied by the selected architecture, each with real controls.

    N11: generic category controls describe WHAT must be true; the selected
    implementation's catalog describes HOW it is done on that platform, so
    "retry policy" becomes "n8n node retry policy + error branch + failure
    queue" when n8n is the chosen build.
    """
    out: list[RiskControl] = []
    seen: set[RiskCategory] = set()

    for cap in capabilities:
        cat = _CAPABILITY_RISKS.get(cap)
        if cat and cat not in seen:
            seen.add(cat)
            out.append(_control(cat, f"architecture uses the '{cap.value}' capability"))

    if integrations:
        out.append(_control(RiskCategory.INTEGRATION_FAILURE,
                            f"{integrations} system(s) to integrate with"))
    if scale_shortfall:
        out.append(_control(RiskCategory.SCALE_LIMIT,
                            "part of the chosen stack is not rated for the assessed volume"))
    if compliance_gap:
        out.append(_control(RiskCategory.COMPLIANCE_GAP,
                            "declared constraints are not covered by the selected stack"))
    if any(m == HitlMode.AUTONOMOUS for m in hitl_modes):
        out.append(_control(RiskCategory.UNREVIEWED_AUTONOMY,
                            "at least one task runs autonomously"))

    if implementation is not None:
        for rc in out:
            rc.implementation_id = implementation.id
            rc.implementation_controls = list(implementation.control_catalog)
    return out
