"""Source-of-truth structured assessment state.

Single Pydantic model shared by every module (see ARCHITECTURE.txt 3.8).
Every field carries a provenance tag. This file defines the schema only;
field/question *definitions* live in interviewer/fields.py.
"""

from __future__ import annotations

from enum import Enum
from typing import Any, Literal, Optional

from pydantic import BaseModel, Field


class Provenance(str, Enum):
    """Five-tag provenance system (spec section 6)."""
    USER_PROVIDED = "user_provided"
    SOURCED = "sourced"
    ESTIMATED = "estimated"
    ASSUMED = "assumed"
    DERIVED = "derived"


class Sector(str, Enum):
    CUSTOMER_SUPPORT = "customer_support"
    DOCUMENT_PROCESSING = "document_processing"


class BuyOrBuild(str, Enum):
    BUY = "buy"
    BUILD = "build"
    UNKNOWN = "unknown"


class ProcessStage(BaseModel):
    stage: str
    required: bool = True
    buy_or_build: BuyOrBuild = BuyOrBuild.UNKNOWN
    vendor_or_approach: Optional[str] = None


class RangeEstimate(BaseModel):
    """A ranged value with a confidence label — never a bare number."""
    min: float
    max: float
    confidence: Literal["low", "medium", "high"] = "medium"


class EffortBand(str, Enum):
    SMALL = "small"
    MEDIUM = "medium"
    LARGE = "large"


class InterviewStatus(str, Enum):
    """The four interviewer states.

    INTERVIEWING: need more information (ask a new field).
    CLARIFYING:   the existing answer is ambiguous/insufficient.
    READY:        minimum sufficient state reached.
    UNCERTAIN:    cannot obtain reliable info after reasonable attempts.
    """
    INTERVIEWING = "interviewing"
    CLARIFYING = "clarifying"
    READY = "ready"
    UNCERTAIN = "uncertain"


class FieldResolution(str, Enum):
    """Per-field resolution state tracked by the interviewer."""
    MISSING = "missing"
    RESOLVED = "resolved"
    AMBIGUOUS = "ambiguous"
    LOW_CONFIDENCE = "low_confidence"
    CONTRADICTORY = "contradictory"
    NEEDS_DETAIL = "needs_detail"


class FieldMeta(BaseModel):
    """Bookkeeping for how the interviewer has handled one field."""
    status: FieldResolution = FieldResolution.MISSING
    attempts: int = 0
    reason: Optional[str] = None


class AiSolution(BaseModel):
    """LLM-proposed AI approach. Numbers stay ranges (spec section 7)."""
    approach: str
    automation_rate: RangeEstimate
    accuracy: RangeEstimate
    integration_complexity: EffortBand
    effort: EffortBand
    technical_risks: list[str] = Field(default_factory=list)


class RiskInputs(BaseModel):
    """Risk split into probability x consequence (spec 9.3)."""
    failure_probability: Optional[RangeEstimate] = None
    failure_impact: Optional[str] = None          # severity band, user-reported
    compliance_exposure: Optional[list[str]] = None
    reliability_gap: Optional[float] = None        # derived: required - achievable


class AssessmentState(BaseModel):
    """The full structured assessment state, tagged per field."""

    sector: Sector
    problem: str = ""

    # Core economic inputs (labor baseline, spec 8.1)
    process: Optional[str] = None
    monthly_volume: Optional[float] = None
    avg_time_per_unit_minutes: Optional[float] = None
    current_headcount: Optional[int] = None
    worker_role: Optional[str] = None
    fully_loaded_annual_cost: Optional[float] = None
    fraction_time_on_process: Optional[float] = None  # 0.0-1.0

    # Feasibility inputs (spec 9.2)
    required_accuracy: Optional[float] = None         # 0.0-1.0
    existing_data: Optional[str] = None               # readiness description
    current_tools: list[str] = Field(default_factory=list)
    integration_complexity: Optional[EffortBand] = None

    # Risk inputs (spec 9.3)
    risk: RiskInputs = Field(default_factory=RiskInputs)

    # Implementation costing (spec 8.4)
    process_stages: list[ProcessStage] = Field(default_factory=list)

    # LLM-proposed solution (spec 7)
    ai_solution: Optional[AiSolution] = None

    # Provenance per field: field_name -> Provenance
    provenance: dict[str, Provenance] = Field(default_factory=dict)

    # Interviewer control
    turn_count: int = 0
    complete: bool = False
    status: InterviewStatus = InterviewStatus.INTERVIEWING
    # Per-field resolution: field_name -> FieldMeta
    field_resolution: dict[str, FieldMeta] = Field(default_factory=dict)

    def get_meta(self, field: str) -> FieldMeta:
        return self.field_resolution.setdefault(field, FieldMeta())

    def set_resolution(self, field: str, status: FieldResolution, reason: Optional[str] = None) -> None:
        meta = self.get_meta(field)
        meta.status = status
        if reason:
            meta.reason = reason

    def tag(self, field: str, provenance: Provenance) -> None:
        self.provenance[field] = provenance

    def get_tag(self, field: str) -> Optional[Provenance]:
        return self.provenance.get(field)

    def get_value(self, field: str) -> Any:
        """Read a field value, resolving dotted paths (e.g. 'risk.failure_impact')."""
        obj: Any = self
        for part in field.split("."):
            if obj is None:
                return None
            obj = getattr(obj, part, None)
        return obj

    def set_value(self, field: str, value: Any) -> None:
        """Write a field value, resolving dotted paths (e.g. 'risk.failure_impact')."""
        parts = field.split(".")
        obj: Any = self
        for part in parts[:-1]:
            child = getattr(obj, part, None)
            if child is None:
                child = type(obj).model_fields[part].default_factory() if \
                    type(obj).model_fields[part].default_factory else None
                setattr(obj, part, child)
            obj = child
        setattr(obj, parts[-1], value)
