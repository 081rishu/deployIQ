"""Deterministic scope model — C2 (engineering effort) and C14 (integration
complexity).

Both bands used to be static: effort was read off the registry entry's
`technical_complexity`, and integration complexity was whatever the user
guessed in the interview. Neither responded to the actual shape of the job.

Here both are computed from named scope factors carried by the assessment.
The LLM may explain the result; it never picks the band.

Every factor is scored from data we genuinely hold. Factors the assessment
does not capture are listed in `unknown_factors` rather than silently scored
as zero — an unmeasured factor is not an absent one.
"""

from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, Field

from schemas.assessment_state import (
    point,
    AssessmentState,
    DataReadiness,
    EffortBand,
    Provenance,
    RangeEstimate,
)
from solution.calibration import CALIBRATION, DISCLOSURE
from solution.constants import scale_band
from solution.schema import Capability, HitlMode, ImplementationKind

# All weights and thresholds live in solution/calibration.py, each carrying a
# rationale, a version and `assumed` provenance (N6). Nothing here is an
# empirical measurement.
_C = CALIBRATION


class ScopeFactor(BaseModel):
    key: str
    label: str
    points: float
    basis: str


class ScopeAssessment(BaseModel):
    band: EffortBand
    score: float
    factors: list[ScopeFactor] = Field(default_factory=list)
    unknown_factors: list[str] = Field(default_factory=list)
    calibration_version: int = 1
    basis: str = ""

    def explain(self) -> str:
        parts = [f"{f.label} +{f.points:g} ({f.basis})" for f in self.factors if f.points]
        return f"{self.band.value} band, score {self.score:g}: " + "; ".join(parts)


def _integration_count(state: AssessmentState) -> int:
    return len([t for t in (state.current_tools or []) if str(t).strip()])


def _scale_of(state: AssessmentState) -> str:
    # N5: one canonical definition, shared with ranking.
    return scale_band(point(state.monthly_volume))


def _shared_factors(state: AssessmentState) -> tuple[list[ScopeFactor], list[str]]:
    factors: list[ScopeFactor] = []
    unknown: list[str] = []

    n = _integration_count(state)
    if n:
        factors.append(ScopeFactor(
            key="integrations", label="Systems to integrate",
            points=min(n * _C.points_per_integration.value,
                       _C.max_integration_points.value),
            basis=f"{n} tool(s) named: {', '.join(state.current_tools)}"))
    else:
        unknown.append("number of systems to integrate (current_tools is empty)")

    if state.data_readiness is not None:
        factors.append(ScopeFactor(
            key="data_readiness", label="Data preparation",
            points=_C.data_readiness_points[state.data_readiness.value].value,
            basis=f"data readiness: {state.data_readiness.value}"))
    else:
        unknown.append("data readiness")

    constraints = list(state.risk.compliance_exposure or [])
    if constraints:
        factors.append(ScopeFactor(
            key="compliance", label="Compliance work",
            points=min(len(constraints) * _C.compliance_points_each.value,
                       _C.max_compliance_points.value),
            basis=f"{len(constraints)} constraint(s): {', '.join(constraints)}"))

    scale = _scale_of(state)
    if point(state.monthly_volume):
        factors.append(ScopeFactor(
            key="scale", label="Scale", points=_C.scale_points[scale].value,
            basis=f"{point(state.monthly_volume):,.0f}/month -> {scale}"))
    else:
        unknown.append("monthly volume")
    return factors, unknown


def effort_scope(
    state: AssessmentState, capabilities: list[Capability],
    hitl_modes: Optional[list[HitlMode]] = None,
    implementation_kind: Optional[ImplementationKind] = None,
) -> ScopeAssessment:
    """C2 + N1: engineering effort from scope, modified by implementation kind.

    D2: scope stays the primary driver; implementation kind is a modifier. The
    same business scope built from scratch should cost more than the same scope
    on a platform that supplies connectors, retries and monitoring — but the
    platform choice must not outweigh the size of the job.
    """
    factors, unknown = _shared_factors(state)

    extra_caps = max(0, len(capabilities) - int(_C.simple_pipeline_capabilities.value))
    if extra_caps:
        factors.append(ScopeFactor(
            key="capability_breadth", label="Capability breadth",
            points=extra_caps * _C.custom_capability_points.value,
            basis=f"{len(capabilities)} capabilities, {extra_caps} beyond a simple pipeline"))

    modes = hitl_modes or []
    if any(m in (HitlMode.HUMAN_REVIEW, HitlMode.ESCALATION) for m in modes):
        factors.append(ScopeFactor(
            key="human_review", label="Human-review tooling",
            points=_C.human_review_points.value,
            basis="at least one task needs review/escalation, which needs a queue and UI"))

    if implementation_kind is not None:
        param = _C.implementation_kind_points[implementation_kind.value]
        factors.append(ScopeFactor(
            key="implementation_kind", label="Implementation overhead",
            points=param.value,
            basis=f"{implementation_kind.value}: {param.rationale}"))
    else:
        unknown.append("implementation kind (no implementation selected yet)")

    unknown.extend([
        "existing automation that could be reused",
        "deployment environment (cloud / on-prem / hybrid)",
    ])
    score = round(sum(f.points for f in factors), 2)
    return ScopeAssessment(
        band=_C.effort_band(score), score=score, factors=factors,
        unknown_factors=unknown, calibration_version=_C.effort_large_threshold.version,
        basis="engineering effort derived from assessed scope and implementation "
              "kind, not from a registry constant. " + DISCLOSURE)


def integration_scope(state: AssessmentState) -> ScopeAssessment:
    """C14: integration-complexity band from deterministic factors."""
    factors, unknown = _shared_factors(state)
    factors = [f for f in factors if f.key != "capability_breadth"]

    if state.sector.value == "customer_support":
        factors.append(ScopeFactor(
            key="realtime", label="Real-time interaction",
            points=_C.realtime_points.value,
            basis="conversational sector: latency and session handling"))

    unknown.extend([
        "API availability for the named systems",
        "authentication requirements",
        "whether any integration must be custom-built",
    ])
    score = round(sum(f.points for f in factors), 2)
    return ScopeAssessment(
        band=_C.integration_band(score), score=score, factors=factors,
        unknown_factors=unknown, calibration_version=_C.integration_large_threshold.version,
        basis="integration complexity derived from assessed factors rather than "
              "asked of the user as an engineering judgement. " + DISCLOSURE)
