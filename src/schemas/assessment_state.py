"""Source-of-truth structured assessment state.

Single Pydantic model shared by every module (see ARCHITECTURE.txt 3.8).
Every field carries a provenance tag. This file defines the schema only;
field/question *definitions* live in interviewer/fields.py.
"""

from __future__ import annotations

from enum import Enum
from typing import Any, Literal, Optional

from pydantic import BaseModel, Field, field_validator


class Provenance(str, Enum):
    """Five-tag provenance system (spec section 6).

    This is the ONLY provenance vocabulary in the system. The solution layer
    previously kept a parallel set; it maps in as:
        benchmark, evidence -> SOURCED   (the specific origin lives in
                                          RangeEstimate.source)
        llm_estimate        -> ESTIMATED
        assumption          -> ASSUMED
        derived             -> DERIVED
    """
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
    """A ranged value with confidence and provenance — never a bare number.

    Spec 7.3/9.6: LLM-estimated quantities are always a range plus a
    confidence label. Spec 6: every value carries exactly one provenance tag,
    with `source` holding the human-readable origin (report citation, the
    reason an assumption was made, or the derivation).
    """
    min: float
    max: float
    confidence: Literal["low", "medium", "high"] = "medium"
    provenance: Provenance = Provenance.ESTIMATED
    # Rendered citation — presentation metadata only (N8).
    source: str = ""
    # Stable evidence identifier. Provenance validation matches on THIS, not on
    # the rendered string, so reformatting a citation cannot invalidate a
    # legitimate evidence relationship.
    source_id: Optional[str] = None


class DataReadiness(str, Enum):
    """Categorical data readiness (spec 9.2).

    Free text cannot deterministically produce a sub-score, so the LLM
    classifies the user's description into one of these bands and code maps the
    band to a number — the same split as the effort bands in 7.4.
    """
    NONE = "none"
    MINIMAL = "minimal"
    PARTIAL = "partial"
    GOOD = "good"
    EXCELLENT = "excellent"


class ImpactSeverity(str, Enum):
    """Consequence severity if the AI produces a wrong output (spec 9.3)."""
    NEGLIGIBLE = "negligible"
    MINOR = "minor"
    MODERATE = "moderate"
    MAJOR = "major"
    SEVERE = "severe"


class ProcessRole(str, Enum):
    """Canonical PROCESS-labor roles (fix spec 12).

    These are the roles the labor-rate registry actually prices. Free-text role
    descriptions are normalised into one of these so a more specific rate can
    be used; an unrecognised description leaves the role unset and the sector
    default applies.

    Implementation (engineering) roles are deliberately NOT here — process and
    implementation labor are separate kinds and must never be interchanged.
    """
    CUSTOMER_SUPPORT_AGENT = "customer_support_agent"
    CUSTOMER_SUPPORT_SPECIALIST = "customer_support_specialist"
    ACCOUNTS_PAYABLE_CLERK = "accounts_payable_clerk"


SECTOR_PROCESS_ROLES = {
    "customer_support": [ProcessRole.CUSTOMER_SUPPORT_AGENT,
                         ProcessRole.CUSTOMER_SUPPORT_SPECIALIST],
    "document_processing": [ProcessRole.ACCOUNTS_PAYABLE_CLERK],
}


class CurrentQualityMetric(str, Enum):
    """Sector-appropriate metrics for the CURRENT process (fix spec 11).

    We never ask "what is your accuracy?" — that is not a number most
    operations hold. We ask for the metric the sector actually tracks, and we
    keep the metric NAME with the value so nothing downstream can silently
    reinterpret one as another (an exception rate is not an accuracy rate).
    """
    # Customer support
    FIRST_CONTACT_RESOLUTION = "first_contact_resolution"
    ESCALATION_RATE = "escalation_rate"
    REWORK_RATE = "rework_rate"
    # Document processing
    EXCEPTION_RATE = "exception_rate"
    FIRST_PASS_YIELD = "first_pass_yield"
    STRAIGHT_THROUGH_RATE = "straight_through_rate"


# Which metrics each sector is actually asked for.
SECTOR_QUALITY_METRICS = {
    "customer_support": [
        CurrentQualityMetric.FIRST_CONTACT_RESOLUTION,
        CurrentQualityMetric.ESCALATION_RATE,
        CurrentQualityMetric.REWORK_RATE,
    ],
    "document_processing": [
        CurrentQualityMetric.EXCEPTION_RATE,
        CurrentQualityMetric.FIRST_PASS_YIELD,
        CurrentQualityMetric.STRAIGHT_THROUGH_RATE,
    ],
}


class ComplianceRequirement(BaseModel):
    """A compliance requirement, normalised for the evidence filter (spec 14).

    `standard` is the canonical registry key the deterministic filter matches
    on. `stated_as` preserves the user's own words so the report can quote them
    and a mis-normalisation is auditable.

    The LLM normalises language to a key. It NEVER decides whether the
    requirement is satisfied — that is the evidence registry's job.
    """
    standard: str
    stated_as: str = ""
    hard_requirement: bool = True


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


class RiskInputs(BaseModel):
    """Risk split into probability x consequence (spec 9.3).

    Two quantities deliberately do NOT live here:

    * failure_probability — DERIVED ONLY. The estimator already holds the
      architecture's performance evidence, and the scoring layer derives raw
      error and then HITL-adjusted residual failure from it. Asking a user to
      estimate a failure probability would be asking them to guess at something
      we can compute, and it would compete with a question about an observable
      fact.
    * reliability_gap — derived in calc/risk_score.py from required vs
      achievable accuracy.
    """

    model_config = {"validate_assignment": True}

    failure_impact: Optional[str] = None          # free-text description
    failure_impact_severity: Optional[ImpactSeverity] = None  # scored category
    compliance_exposure: Optional[list[str]] = None      # canonical keys
    # The same requirements with the user's original phrasing preserved.
    compliance_requirements: list[ComplianceRequirement] = Field(default_factory=list)


def point(value: Any) -> Optional[float]:
    """Midpoint of a ranged value, as an EXPLICIT derived transformation (§9).

    User-provided ranges stay ranges in the state. Any calculation that needs a
    single number calls this, so the collapse is visible at the call site
    rather than happening silently at collection time.
    """
    if value is None:
        return None
    if isinstance(value, RangeEstimate):
        return (value.min + value.max) / 2.0
    if isinstance(value, dict) and "min" in value and "max" in value:
        return (float(value["min"]) + float(value["max"])) / 2.0
    if isinstance(value, (int, float)):
        return float(value)
    return None


def as_range(value: Any, *, provenance: "Provenance" = None,
             source: str = "") -> Optional["RangeEstimate"]:
    """Coerce a scalar or dict into the canonical ranged type."""
    if value is None:
        return None
    if isinstance(value, RangeEstimate):
        return value
    prov = provenance or Provenance.USER_PROVIDED
    if isinstance(value, dict) and "min" in value and "max" in value:
        lo, hi = float(value["min"]), float(value["max"])
        return RangeEstimate(min=min(lo, hi), max=max(lo, hi),
                             provenance=prov, source=source)
    if isinstance(value, (int, float)):
        return RangeEstimate(min=float(value), max=float(value),
                             provenance=prov, source=source)
    return None


# Geography -> currency is derived, never asked separately (fix spec 4).
GEOGRAPHY_CURRENCY = {
    "india": "INR",
    "in": "INR",
    "us": "USD",
    "usa": "USD",
    "united states": "USD",
}


class AssessmentState(BaseModel):
    """The full structured assessment state, tagged per field."""

    # Fix spec 8: assignment is validated. `set_value` previously used bare
    # setattr, so every type guarantee in this schema was advisory and a dict
    # could sit in a float field undetected.
    model_config = {"validate_assignment": True}

    sector: Sector
    problem: str = ""
    # Where the assessed workforce sits. Controls labor-rate and currency
    # resolution (E8) — an unknown geography must never silently inherit US
    # rates, so this stays Optional and unresolved rather than defaulted.
    geography: Optional[str] = None

    @property
    def currency(self) -> Optional[str]:
        """Derived from geography — never collected separately, never
        defaulted to USD (fix spec 4)."""
        if not self.geography:
            return None
        return GEOGRAPHY_CURRENCY.get(self.geography.strip().lower())

    # Core economic inputs (labor baseline, spec 8.1)
    process: Optional[str] = None
    # Fix spec 9: a range the user gave stays a range. Call `point()` where a
    # single number is needed, so the midpoint is an explicit derivation.
    monthly_volume: Optional[RangeEstimate] = None
    avg_time_per_unit_minutes: Optional[RangeEstimate] = None
    current_headcount: Optional[RangeEstimate] = None
    worker_role: Optional[str] = None            # the user's own words
    worker_role_canonical: Optional[ProcessRole] = None   # registry lookup key
    fully_loaded_annual_cost: Optional[RangeEstimate] = None
    fraction_time_on_process: Optional[RangeEstimate] = None  # 0.0-1.0

    # Other current-cost components (spec 8.2 / E1). All optional: the user is
    # never pushed to invent a number, and an uncollected component stays
    # ABSENT rather than becoming zero.
    # Asked as ranges, so stored as ranges (spec 9) — call point() downstream.
    annual_tooling_cost: Optional[RangeEstimate] = None
    monthly_tooling_cost: Optional[RangeEstimate] = None
    error_rate: Optional[RangeEstimate] = None          # fraction 0-1 of units
    rework_time_per_error_minutes: Optional[RangeEstimate] = None
    annual_rework_cost: Optional[RangeEstimate] = None  # if already known directly
    annual_other_direct_cost: Optional[RangeEstimate] = None
    other_direct_cost_description: Optional[str] = None

    # Current-process quality (spec 8.6 / E6). Metric NAME and value are kept
    # together; without both the comparison stays ABSENT rather than assuming
    # the current process is perfect.
    current_quality_metric: Optional[CurrentQualityMetric] = None
    current_quality_value: Optional[RangeEstimate] = None

    # Feasibility inputs (spec 9.2)
    required_accuracy: Optional[RangeEstimate] = None  # 0.0-1.0
    existing_data: Optional[str] = None               # readiness description
    data_readiness: Optional[DataReadiness] = None    # scored category (9.2)
    current_tools: list[str] = Field(default_factory=list)
    integration_complexity: Optional[EffortBand] = None

    # Risk inputs (spec 9.3)
    risk: RiskInputs = Field(default_factory=RiskInputs)

    # Implementation costing (spec 8.4)
    process_stages: list[ProcessStage] = Field(default_factory=list)

    # Provenance per field: field_name -> Provenance
    provenance: dict[str, Provenance] = Field(default_factory=dict)

    # Interviewer control
    turn_count: int = 0
    complete: bool = False
    status: InterviewStatus = InterviewStatus.INTERVIEWING
    # Per-field resolution: field_name -> FieldMeta
    field_resolution: dict[str, FieldMeta] = Field(default_factory=dict)

    @field_validator("current_quality_value", "annual_tooling_cost",
                     "monthly_tooling_cost", "error_rate",
                     "rework_time_per_error_minutes", "annual_rework_cost",
                     "annual_other_direct_cost", "monthly_volume",
                     "avg_time_per_unit_minutes",
                     "current_headcount", "fully_loaded_annual_cost",
                     "fraction_time_on_process", "required_accuracy",
                     mode="before")
    @classmethod
    def _coerce_range(cls, v):
        """Accept a scalar or {min,max} and store a RangeEstimate.

        A single number becomes a point range (min == max) — it is not a
        fabricated spread, and a genuine range is never collapsed.
        """
        if v is None or isinstance(v, RangeEstimate):
            return v
        coerced = as_range(v)
        if coerced is None:
            raise ValueError(f"cannot interpret {v!r} as a numeric value or range")
        return coerced

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

    def set_value(self, field: str, value: Any,
                  provenance: Optional[Provenance] = None) -> None:
        """Write a field value, resolving dotted paths.

        Coerces scalars/dicts into RangeEstimate for range-typed fields, then
        assigns through pydantic validation. A rejected value raises and the
        previous valid value is preserved (fix spec 8).
        """
        parts = field.split(".")
        obj: Any = self
        for part in parts[:-1]:
            child = getattr(obj, part, None)
            if child is None:
                child = type(obj).model_fields[part].default_factory() if \
                    type(obj).model_fields[part].default_factory else None
                setattr(obj, part, child)
            obj = child

        leaf = parts[-1]
        info = type(obj).model_fields.get(leaf)
        if info is not None and "RangeEstimate" in str(info.annotation) \
                and value is not None:
            coerced = as_range(value, provenance=provenance,
                               source=f"user-provided {field}")
            if coerced is None:
                # Unparseable input must RAISE, not clear the field. Returning
                # None here would silently wipe a good value with garbage.
                raise ValueError(
                    f"{field}: cannot interpret {value!r} as a numeric value or "
                    f"range; the previous value is preserved")
            value = coerced
        setattr(obj, leaf, value)
