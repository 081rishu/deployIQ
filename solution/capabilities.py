"""LLM decomposition of the assessed workflow into required capabilities.

The LLM only decomposes (proposes); it never selects pattern IDs. Output is
constrained to the Capability enum so matching stays deterministic.
"""

from __future__ import annotations

import json

from llm.openai_client import complete_json
from schemas.assessment_state import AssessmentState
from solution.schema import Capability

_CAPS = [c.value for c in Capability]


def decompose(state: AssessmentState) -> list[Capability]:
    system = (
        "You decompose a business workflow into the minimal set of capabilities "
        "needed to automate it. Return ONLY JSON with one key 'capabilities', a "
        f"list of values chosen from: {_CAPS}. Do not invent new capabilities."
    )
    user = (
        f"Workflow problem: {state.problem}\n"
        f"Process: {state.process}\n"
        f"Sector: {state.sector.value}\n"
        f"Existing data: {state.existing_data}\n"
        f"Integration complexity: {state.integration_complexity}\n"
        f"Compliance: {state.risk.compliance_exposure if state.risk else None}"
    )
    result = complete_json(system, user)
    raw = result.get("capabilities", [])
    known = {c.value: c for c in Capability}
    return [known[v] for v in raw if v in known]
