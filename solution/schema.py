"""Solution estimator output schema.

Every numeric estimate is a range + confidence. HITL is first-class. Task-level
automation is estimated per task, then aggregated.
"""

from __future__ import annotations

from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field

# Provenance and RangeEstimate are defined ONCE, in the source-of-truth state
# module (ARCHITECTURE.txt 3.8 / spec 6). The solution layer used to keep its
# own parallel copies; consumers now import them from here or from
# schemas.assessment_state directly — both names refer to the same class.
from schemas.assessment_state import (  # noqa: F401  (re-exported)
    EffortBand,
    Provenance,
    RangeEstimate,
    Sector,
)


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


class ImplementationKind(str, Enum):
    """Canonical implementation category (N1, guardrail 9).

    Declared explicitly by the registry rather than inferred from provider
    names, because it now modifies engineering effort.
    """
    LOW_CODE = "low_code"
    MANAGED_SERVICE = "managed_service"
    CUSTOM_CODE = "custom_code"


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


class ComplianceStatus(str, Enum):
    """Finesse spec 5: a claim is supported ONLY with evidence behind it.

    `unknown` is the honest default and can never satisfy a requirement.
    Preferring `unknown` over an unsourced assertion is deliberate: compliance
    influences architecture selection, so an unbacked "yes" is worse than an
    admitted gap.
    """
    SUPPORTED = "supported"
    UNSUPPORTED = "unsupported"
    UNKNOWN = "unknown"


class ComplianceClaim(BaseModel):
    standard: str
    status: ComplianceStatus = ComplianceStatus.UNKNOWN
    evidence_id: Optional[str] = None
    reason: str = ""

    def satisfies(self) -> bool:
        """Only an evidence-backed SUPPORTED claim satisfies a requirement."""
        return self.status == ComplianceStatus.SUPPORTED and bool(self.evidence_id)


class Compatibility(BaseModel):
    """Explicit metadata used by filtering/ranking — not arbitrary rules."""
    supported_capabilities: list[Capability] = Field(default_factory=list)
    supported_integrations: list[str] = Field(default_factory=list)
    scale: str = "any"                 # small | medium | large | any
    latency: str = "medium"            # low | medium | high
    deployment: str = "cloud"          # cloud | on_prem | hybrid
    compliance: list[ComplianceClaim] = Field(default_factory=list)
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
    kind: ImplementationKind
    compatibility: Compatibility
    providers: list[TechnologyProvider] = Field(default_factory=list)
    # Registry hardening: an implementation declares what it covers itself,
    # so a pattern cannot qualify on the strength of a sibling implementation.
    version: int = 1
    last_reviewed: str = ""
    control_catalog: list[str] = Field(default_factory=list)   # N11


class SolutionPattern(BaseModel):
    id: str
    name: str
    architecture: str
    implementations: list[ImplementationOption] = Field(default_factory=list)


class DeviationTrigger(str, Enum):
    """What makes departing from the reference architecture legitimate.

    Free text cannot be evaluated, so each condition names a trigger the
    ranker can actually test. MANUAL is the honest escape hatch: the condition
    is real but depends on a fact the assessment does not capture, so it is
    surfaced for human judgement instead of being silently ignored.
    """
    MONTHLY_VOLUME_ABOVE = "monthly_volume_above"
    COMPLIANCE_PRESENT = "compliance_present"
    REQUIRED_ACCURACY_ABOVE = "required_accuracy_above"
    MANUAL = "manual"


class DeviationCondition(BaseModel):
    """One condition under which the reference solution stops being the
    expected answer. `description` is what the report shows; the trigger and
    threshold are what the ranker evaluates."""
    id: str
    description: str
    trigger: DeviationTrigger
    threshold: Optional[float] = None
    # Implementation kinds this condition legitimises (e.g. "custom" once
    # volume outgrows a low-code build). Empty = no directed preference.
    releases_to_kinds: list[str] = Field(default_factory=list)


class ReferenceSolution(BaseModel):
    id: str
    sectors: list[Sector]
    pattern: str
    expected_capabilities: list[Capability]
    recommended_architecture: str
    rationale: str
    conditions_for_deviation: list[DeviationCondition] = Field(default_factory=list)


class TaskAutomationEstimate(BaseModel):
    task: str
    capability: Capability
    architecture: str        # the selected architecture this estimate is tied to
    benchmark_basis: str     # LLM prose ONLY — never used as provenance (C1)
    estimate: RangeEstimate
    hitl: HitlMode = HitlMode.AI_ASSISTED
    # Derived from handling time by calc, never supplied by the LLM (C5).
    workload_share: float = 0.0
    workload_share_provenance: Provenance = Provenance.DERIVED
    handling_time_minutes: Optional[RangeEstimate] = None
    benchmark_anchor: Optional[str] = None      # citation of the anchor used (C4)
    divergence_note: str = ""                   # set when the claim outruns evidence


class ReferenceComparison(BaseModel):
    """Explicit reference-vs-selected comparison (P1, spec 7.1).

    Carries the same alignment number the ranker used, so the report can state
    why the selected architecture did or did not follow the curated baseline
    rather than asserting it after the fact.
    """
    reference_id: str
    expected_pattern: str
    selected_pattern: str
    match: bool
    alignment: float                 # 0-1, the factor that entered ranking
    deviation_reason: str = ""
    # Conditions that fired, in the reference's own words.
    active_deviations: list[str] = Field(default_factory=list)
    # Real conditions that could not be evaluated from the assessment state.
    unevaluated_conditions: list[str] = Field(default_factory=list)


class OperatingCostInputs(BaseModel):
    """Technical cost components the Economic Engine needs (C12, spec 8.4).

    The estimator exposes the drivers; annual economics stay the engine's job.
    A component with no sourced basis is named here as absent rather than
    omitted, so the engine can report the gap.
    """
    inference_price_per_unit: Optional[RangeEstimate] = None
    inference_basis: str = ""
    human_review_share: Optional[float] = None      # share of tasks needing review
    absent_components: list[str] = Field(default_factory=list)


class SolutionEstimate(BaseModel):
    """The full estimator output (spec 7.2)."""
    recommended_pattern: str
    # The implementation actually selected for the recommended pattern. Needed
    # by the alternatives module (spec 11) so it can exclude the primary and
    # compare against the build that was really chosen, not the pattern alone.
    recommended_implementation: str = ""
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

    # --- added by the C1-C14 fixes ---------------------------------------
    # Scope-derived bands and their reasoning (C2, C14).
    effort_basis: str = ""
    integration_basis: str = ""
    integration_complexity_reported: Optional[EffortBand] = None  # what the user said
    engineering_cost: Optional[RangeEstimate] = None              # C3
    # Capability decomposition validation (C8, C9).
    capability_validation: Optional[dict] = None
    # Structured risk controls (C13); risks_and_mitigations is derived from it.
    risk_controls: list[dict] = Field(default_factory=list)
    # Cost drivers for the Economic Engine (C12).
    operating_cost_inputs: Optional[OperatingCostInputs] = None
    # Overall confidence and why (C10, C12).
    assessment_confidence: str = "medium"
    confidence_score: float = 0.0
    confidence_notes: list[str] = Field(default_factory=list)
    # N4: how the task decomposition compared to the observed handling time.
    time_reconciliation: Optional[dict] = None
    # Provenance integrity actions taken (C1).
    provenance_warnings: list[str] = Field(default_factory=list)
    # Hard compliance filtering (evidence registry). When `compliance_gap` is
    # true no architecture is recommended, and `compliance_exclusions` records
    # every candidate that was removed and why.
    compliance_gap: bool = False
    compliance_statement: str = ""
    compliance_exclusions: list[dict] = Field(default_factory=list)
    compliance_verdicts: list[dict] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Alternatives (spec 11)
#
# Alternatives are INFORMATIONAL. They do not override, modify or re-rank the
# primary selection (11.4), they carry no second recommendation score, and no
# separate economic model is built for them (11.8). Every field below is either
# copied from the Solution Registry, derived by the same deterministic code
# that produced the primary estimate, or LLM prose that has been through the
# guard in solution/alternatives.py.
# ---------------------------------------------------------------------------


class AlternativeSource(str, Enum):
    """Where an alternative's facts come from.

    REGISTRY is the only source that can describe an architecture. The LLM may
    never introduce an entry outside it (11.5).
    """
    REGISTRY = "registry"
    CURRENT_PROCESS = "current_process"   # the user's own process, from AssessmentState


class DifferenceKind(str, Enum):
    """How an alternative is materially different from the primary (11.1).

    A vendor swap inside the same pattern and the same implementation kind is
    deliberately NOT a difference — presenting it as an alternative would pad
    the section without giving the user a real choice.
    """
    ARCHITECTURE = "different_architecture"
    IMPLEMENTATION_MODEL = "same_architecture_different_implementation_model"
    NO_AI_BASELINE = "no_ai_baseline"


class HumanInvolvement(str, Enum):
    REVIEW_IN_LINE = "review_in_line"
    ESCALATION_ONLY = "escalation_only"
    NOT_DECLARED = "not_declared"
    FULLY_HUMAN = "fully_human"


class AlternativeComparison(BaseModel):
    """The qualitative comparison required by spec 11.3."""
    approach: str                                   # registry: pattern.architecture
    strengths: list[str] = Field(default_factory=list)      # registry
    limitations: list[str] = Field(default_factory=list)    # registry
    implementation_complexity: Optional[EffortBand] = None  # derived by solution.scope
    implementation_complexity_basis: str = ""
    # Registry/benchmark-backed performance for this architecture. NOT a fresh
    # automation estimate — no per-task LLM estimation is run for alternatives.
    expected_automation: list[PerformanceMetric] = Field(default_factory=list)
    automation_basis: str = ""
    human_involvement: HumanInvolvement = HumanInvolvement.NOT_DECLARED
    human_involvement_basis: list[str] = Field(default_factory=list)
    risks: list[str] = Field(default_factory=list)
    # Deterministic differences that would favour this alternative. Empty is a
    # legitimate answer and is reported as such rather than filled with prose.
    when_preferable: list[str] = Field(default_factory=list)


class Alternative(BaseModel):
    id: str
    name: str
    source: AlternativeSource = AlternativeSource.REGISTRY
    pattern_id: str = ""
    implementation_id: str = ""
    implementation_kind: Optional[ImplementationKind] = None
    difference_kind: DifferenceKind = DifferenceKind.ARCHITECTURE
    difference_from_primary: str = ""
    comparison: AlternativeComparison
    uncertainties: list[str] = Field(default_factory=list)
    # LLM prose, after the numeric/recommendation guard. Empty when the model
    # was unavailable or everything it produced was scrubbed.
    explanation: str = ""
    # The primary ranker's own score for this candidate, reused for display
    # order only (11.4: no second recommendation score is calculated).
    ranking_score: Optional[float] = None


class RejectedAlternative(BaseModel):
    """A candidate that did not become an alternative, and why.

    Recorded rather than dropped so the absence of alternatives is auditable
    (11.1: never fabricate alternatives to reach a target count).
    """
    pattern_id: str = ""
    implementation_id: str = ""
    reason: str


class AlternativesResult(BaseModel):
    alternatives: list[Alternative] = Field(default_factory=list)
    # Shown when nothing credible survived (11.7).
    statement: str = ""
    rejected: list[RejectedAlternative] = Field(default_factory=list)
    # Categories named in 11.2 that the curated registry cannot supply. Named
    # explicitly so the gap reads as a registry gap, not as a judgement that
    # the approach is unsuitable.
    categories_not_in_registry: list[str] = Field(default_factory=list)
    ordering_basis: str = ""
    llm_guard_notes: list[str] = Field(default_factory=list)
    # 11.6/11.8: constant, and carried into the payload so a downstream report
    # layer cannot present this section as advice or as a costed comparison.
    is_recommendation: bool = False
    economics_included: bool = False
