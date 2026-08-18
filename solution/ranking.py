"""Deterministic filter + rank of candidate solution patterns.

Uses Compatibility metadata (not arbitrary rules): a pattern is filtered out
only if its implementations genuinely cannot satisfy the assessment's
requirements, then survivors are scored by fit.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from schemas.assessment_state import AssessmentState, EffortBand, Sector
from solution.schema import Capability, SolutionPattern, TechnologyProvider


@dataclass
class RankedCandidate:
    pattern: SolutionPattern
    chosen_implementation: str
    score: float
    reasons: list[str]
    risks: list[str]


def _effective_scale(state: AssessmentState) -> str:
    vol = state.monthly_volume or 0
    if vol >= 50000:
        return "large"
    if vol >= 10000:
        return "medium"
    return "small"


def _needs_compliance(state: AssessmentState) -> list[str]:
    return list(state.risk.compliance_exposure or [])


def _candidate_provider_impl(pattern: SolutionPattern, caps: set[Capability]) -> tuple[str, list[str]]:
    """Pick the least-complex implementation that covers all caps; return
    (impl_id, covered_caps). Deterministic. Considers both implementation and
    provider capabilities."""
    order = {EffortBand.SMALL: 0, EffortBand.MEDIUM: 1, EffortBand.LARGE: 2}
    best_impl = None
    best_covered: set[Capability] = set()
    for impl in pattern.implementations:
        covered: set[Capability] = set(impl.compatibility.supported_capabilities)
        for prov in impl.providers:
            covered |= set(prov.compatibility.supported_capabilities)
        if caps <= covered:
            if best_impl is None or order[impl.compatibility.technical_complexity] < order[best_impl.compatibility.technical_complexity]:
                best_impl = impl
                best_covered = covered
    return (best_impl.id if best_impl else ""), [c.value for c in best_covered]


def filter_and_rank(
    state: AssessmentState,
    candidates: list[SolutionPattern],
    caps: set[Capability],
) -> list[RankedCandidate]:
    scale = _effective_scale(state)
    compliance = _needs_compliance(state)
    ranked: list[RankedCandidate] = []

    for pattern in candidates:
        impl_id, _ = _candidate_provider_impl(pattern, caps)
        if not impl_id:
            continue  # no implementation covers all required capabilities

        reasons: list[str] = []
        risks: list[str] = []
        score = 0.0
        covered = False
        for impl in pattern.implementations:
            compats = [impl.compatibility] + [prov.compatibility for prov in impl.providers]
            for c in compats:
                # Scale fit.
                if c.scale == scale or c.scale == "any":
                    score += 2
                elif c.scale != "any":
                    reasons.append(f"scale '{c.scale}' vs need '{scale}'")
                # Compliance fit.
                if compliance:
                    if all(x in c.compliance for x in compliance):
                        score += 2
                    else:
                        reasons.append(f"missing compliance {compliance}")
                        risks.append("compliance gap")
                covered = True
        if not covered:
            continue
        # Prefer lower technical complexity for MVP.
        score += 1.0
        ranked.append(RankedCandidate(
            pattern=pattern, chosen_implementation=impl_id,
            score=score, reasons=reasons, risks=risks,
        ))

    ranked.sort(key=lambda r: r.score, reverse=True)
    return ranked
