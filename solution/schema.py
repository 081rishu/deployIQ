"""Solution estimator output schema.

Every numeric estimate is a range + confidence. HITL is first-class. Task-level
automation is estimated per task, then aggregated.
"""

from __future__ import annotations

from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field

from schemas.assessment_state import EffortBand, Sector


class Provenance(str, Enum):
    """Source of an estimate value."""
    BENCHMARK = "benchmark"
    EVIDENCE = "evidence"
    LLM_ESTIMATE = "llm_estimate"
    ASSUMPTION = "assumption"
    DERIVED = "derived"


class RangeEstimate(BaseModel):
    """A ranged value with confidence and provenance — never a bare number."""
    min: float
    max: float
    confidence: str = "medium"  # low | medium | high
    provenance: Provenance = Provenance.LLM_ESTIMATE
    source: str = ""            # reason/reference when applicable


class PerformanceMetric(BaseModel):
    """Task/architecture-specific performance, per the selected architecture."""
    metric: str                 # e.g. resolution_rate, escalation_rate, extraction_accuracy, stp_rate
    estimate: RangeEstimate


class SectorPerformance(BaseModel):
    """Sector-specific set of performance metrics (P0.1)."""
    sector: Sector
    metrics: dict[str, list[PerformanceMetric]] = Field(default_factory=dict)  # task -> metrics


class HitlMode(str, Enum):
    AUTONOMOUS = "autonomous"
    AI_ASSISTED = "ai_assisted"
    HUMAN_REVIEW = "human_review"
    HUMAN_ONLY = "human_only"
    ESCALATION = "escalation"


class Capability(str, Enum):
    INGEST = "ingest"
    CLASSIFY = "classify"
    EXTRACT = "extract"
    GENERATE = "generate"
    SEARCH_RETRIEVE = "search_retrieve"
    ROUTE = "route"
    HUMAN_ESCALATE = "human_escalate"
    HUMAN_REVIEW = "human_review"
    POST_PROCESS = "post_process"
    VALIDATE = "validate"


class Compatibility(BaseModel):
    """Explicit metadata used by filtering/ranking — not arbitrary rules."""
    supported_capabilities: list[Capability] = Field(default_factory=list)
    supported_integrations: list[str] = Field(default_factory=list)
    scale: str = "any"                 # small | medium | large | any
    latency: str = "medium"            # low | medium | high
    deployment: str = "cloud"          # cloud | on_prem | hybrid
    compliance: list[str] = Field(default_factory=list)
    technical_complexity: EffortBand = EffortBand.SMALL
    strengths: list[str] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)


class TechnologyProvider(BaseModel):
    id: str
    name: str
    category: str            # llm | voice | orchestration | managed_ai | etc
    compatibility: Compatibility


class ImplementationOption(BaseModel):
    id: str
    name: str
    kind: str                # low_code | custom | managed_service
    compatibility: Compatibility
    providers: list[TechnologyProvider] = Field(default_factory=list)


class SolutionPattern(BaseModel):
    id: str
    name: str
    architecture: str
    implementations: list[ImplementationOption] = Field(default_factory=list)


class ReferenceSolution(BaseModel):
    id: str
    sectors: list[Sector]
    pattern: str
    expected_capabilities: list[Capability]
    recommended_architecture: str
    rationale: str
    conditions_for_deviation: list[str] = Field(default_factory=list)


class TaskAutomationEstimate(BaseModel):
    task: str
    capability: Capability
    architecture: str        # the selected architecture this estimate is tied to
    benchmark_basis: str
    estimate: RangeEstimate
    hitl: HitlMode = HitlMode.AI_ASSISTED
    workload_share: float = 1.0   # fraction of total work this task represents (0-1)


class ReferenceComparison(BaseModel):
    """Explicit reference-vs-selected comparison (P1)."""
    reference_id: str
    match: bool
    deviation_reason: str


class SolutionEstimate(BaseModel):
    """The full estimator output (spec 7.2)."""
    recommended_pattern: str
    candidate_implementations: list[str] = Field(default_factory=list)
    task_automation: list[TaskAutomationEstimate] = Field(default_factory=list)
    overall_automation: RangeEstimate
    performance: list[PerformanceMetric] = Field(default_factory=list)
    reference_comparison: Optional[ReferenceComparison] = None
    integration_complexity: EffortBand
    engineering_effort: EffortBand
    engineering_hours: RangeEstimate
    hitl_requirements: dict[str, HitlMode] = Field(default_factory=dict)
    risks_and_mitigations: list[dict[str, str]] = Field(default_factory=list)
    key_uncertainties: list[str] = Field(default_factory=list)
    fit_explanations: list[str] = Field(default_factory=list)
    needs_more_information: list[str] = Field(default_factory=list)
