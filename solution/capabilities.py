"""LLM workflow decomposition, validated — C9 and C8.

C9: the LLM returns capabilities as EXACT enum members. The previous
substring matcher ("post" -> POST_PROCESS, "complex" -> HUMAN_ESCALATE) could
silently mis-map "post the invoice to the ledger" or match three rules at
once, and dropped anything it failed to match with no record. Now: strict
parse, one constrained retry, then safe failure with the unparsed values
reported.

C8: the decomposition is compared against the sector's curated reference
capabilities. A decomposition that silently omits human_review changes the
pattern, the effort band and the cost, so a mismatch is surfaced rather than
trusted.

The LLM still only decomposes. It never sees or selects a pattern ID.
"""

from __future__ import annotations

import json
from typing import Optional

from pydantic import BaseModel, Field

from lib.logging_config import get_logger
from llm.openai_client import complete_json
from schemas.assessment_state import AssessmentState
from solution.reference_solutions import reference_for
from solution.schema import Capability

log = get_logger("solution.capabilities")

_CAPS = [c.value for c in Capability]
_CAP_LOOKUP = {c.value: c for c in Capability}


class CapabilityValidation(BaseModel):
    """C8: how the decomposition compares to the curated baseline."""
    capabilities: list[Capability] = Field(default_factory=list)
    unparsed: list[str] = Field(default_factory=list)
    missing_vs_reference: list[Capability] = Field(default_factory=list)
    extra_vs_reference: list[Capability] = Field(default_factory=list)
    reference_id: Optional[str] = None
    valid: bool = True
    notes: list[str] = Field(default_factory=list)


def parse_capability(value: str) -> Optional[Capability]:
    """Strict enum parse. Case and surrounding whitespace are tolerated;
    meaning is not guessed."""
    return _CAP_LOOKUP.get(str(value or "").strip().lower())


def _decompose_once(state: AssessmentState, strict_retry: bool) -> tuple[list[Capability], list[str]]:
    system = (
        "You decompose a business workflow into the minimal set of capabilities "
        "needed to automate it.\n\n"
        f"Return ONLY JSON: {{\"capabilities\": [...]}} where every element is "
        f"EXACTLY one of these strings: {_CAPS}.\n"
        "Do not invent capabilities. Do not describe them. Do not return prose, "
        "synonyms, or capitalised variants — return the exact strings listed."
    )
    if strict_retry:
        system += ("\n\nYour previous response contained values outside that list. "
                   "Return only exact members of the list.")
    user = (
        f"Workflow problem: {state.problem}\n"
        f"Process: {state.process}\n"
        f"Sector: {state.sector.value}\n"
        f"Existing data: {state.existing_data}\n"
        f"Compliance: {state.risk.compliance_exposure if state.risk else None}"
    )
    result = complete_json(system, user)
    caps, unparsed = [], []
    for raw in result.get("capabilities", []) or []:
        cap = parse_capability(raw)
        if cap is None:
            unparsed.append(str(raw))
        elif cap not in caps:
            caps.append(cap)
    return caps, unparsed


def decompose(state: AssessmentState) -> CapabilityValidation:
    """Decompose, validate against the enum, then against the reference."""
    caps, unparsed = _decompose_once(state, strict_retry=False)
    if unparsed:
        log.warning("capability decomposition returned %d unparsable value(s): %s",
                    len(unparsed), unparsed)
        retry_caps, retry_unparsed = _decompose_once(state, strict_retry=True)
        if len(retry_unparsed) < len(unparsed):
            caps, unparsed = retry_caps, retry_unparsed

    validation = CapabilityValidation(capabilities=caps, unparsed=unparsed)
    if unparsed:
        validation.notes.append(
            f"{len(unparsed)} value(s) could not be parsed as capabilities even "
            f"after a constrained retry: {unparsed}. They are reported rather "
            f"than guessed at.")
    if not caps:
        validation.valid = False
        validation.notes.append(
            "no valid capabilities were decomposed — the estimator cannot "
            "select an architecture from an empty capability set")
        return validation

    reference = reference_for(state.sector)
    if reference is not None:
        validation.reference_id = reference.id
        expected = set(reference.expected_capabilities)
        got = set(caps)
        validation.missing_vs_reference = sorted(expected - got, key=lambda c: c.value)
        validation.extra_vs_reference = sorted(got - expected, key=lambda c: c.value)
        if validation.missing_vs_reference:
            validation.notes.append(
                "decomposition omits capabilities the sector baseline "
                f"({reference.id}) expects: "
                f"{[c.value for c in validation.missing_vs_reference]}. This "
                f"changes which patterns qualify, so it is surfaced rather than "
                f"silently accepted.")
        if validation.extra_vs_reference:
            validation.notes.append(
                "decomposition adds capabilities beyond the baseline: "
                f"{[c.value for c in validation.extra_vs_reference]}.")
    return validation
