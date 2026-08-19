"""Shared handling-time reconciliation policy — D3.

ONE implementation of D3, imported by both the Solution Estimator and the
Economic Engine. Implementing the policy twice is how the same
AssessmentState ends up producing two different labor baselines, which is
precisely what this module exists to prevent.

Policy (locked):
    When the user's observed aggregate handling time is resolved and reliable
    it is AUTHORITATIVE. LLM task-level times supply proportions only, and are
    rescaled so they sum exactly to the observed aggregate.

    With no observed aggregate, the task-derived total may be used, but stays
    tagged `estimated` and lowers confidence.

    A severe contradiction is not normalised away — it is reported, and can
    block the estimate.
"""

from __future__ import annotations

from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field

from schemas.assessment_state import Provenance, RangeEstimate

MIN_TOTAL = 1e-9

# MVP calibration, not scientific thresholds.
DIVERGENCE_MODERATE = 0.25
DIVERGENCE_LARGE = 0.60
DIVERGENCE_SEVERE = 1.50


class DivergenceSeverity(str, Enum):
    NONE = "none"
    MODERATE = "moderate"
    LARGE = "large"
    SEVERE = "severe"


class TimeReconciliation(BaseModel):
    """How a task decomposition compares to the observed aggregate."""
    user_total_minutes: Optional[float] = None
    model_total_minutes: Optional[float] = None
    divergence: Optional[float] = None
    severity: DivergenceSeverity = DivergenceSeverity.NONE
    reconciled: bool = False
    reconciled_times: list[float] = Field(default_factory=list)
    shares: list[float] = Field(default_factory=list)
    authoritative_total_minutes: Optional[float] = None
    total_provenance: Provenance = Provenance.DERIVED
    statement: str = ""
    warnings: list[str] = Field(default_factory=list)

    @property
    def blocks_estimate(self) -> bool:
        return self.severity == DivergenceSeverity.SEVERE

    @property
    def confidence_penalty(self) -> float:
        return {DivergenceSeverity.NONE: 0.0, DivergenceSeverity.MODERATE: 0.07,
                DivergenceSeverity.LARGE: 0.15, DivergenceSeverity.SEVERE: 0.30}[self.severity]


def severity_for(divergence: float) -> DivergenceSeverity:
    if divergence >= DIVERGENCE_SEVERE:
        return DivergenceSeverity.SEVERE
    if divergence >= DIVERGENCE_LARGE:
        return DivergenceSeverity.LARGE
    if divergence >= DIVERGENCE_MODERATE:
        return DivergenceSeverity.MODERATE
    return DivergenceSeverity.NONE


def sanity_warnings(times: list[Optional[float]], names: list[str]) -> list[str]:
    """Lightweight task-time validation (finesse spec section 10).

    Flags obvious nonsense only. It never silently substitutes a corrected
    value — a suspicious estimate is reported, not repaired.
    """
    out = []
    for name, t in zip(names, times):
        if t is None:
            continue
        if t <= 0:
            out.append(f"task {name!r} has a non-positive handling time ({t})")
        elif t < 0.1:
            out.append(f"task {name!r} claims under 6 seconds of human time ({t} min) "
                       f"— implausible for a task a person performs")
        elif t > 480:
            out.append(f"task {name!r} claims over a working day per unit ({t} min)")
    return out


def reconcile(
    task_times: list[Optional[float]], task_names: list[str],
    observed_total_minutes: Optional[float],
    observed_is_reliable: bool = True,
) -> TimeReconciliation:
    """Reconcile a task decomposition against the observed aggregate (D3)."""
    warnings = sanity_warnings(task_times, task_names)
    usable = [t for t in task_times if t is not None and t > 0]

    if not usable:
        return TimeReconciliation(
            user_total_minutes=observed_total_minutes,
            authoritative_total_minutes=observed_total_minutes,
            total_provenance=(Provenance.USER_PROVIDED if observed_total_minutes
                              else Provenance.ASSUMED),
            statement="no usable per-task times; the observed aggregate stands alone",
            warnings=warnings)

    model_total = sum(t for t in task_times if t)
    shares = [(t / model_total if t else 0.0) for t in task_times]

    if not observed_total_minutes or observed_total_minutes <= 0 or not observed_is_reliable:
        reason = ("no observed aggregate handling time" if not observed_total_minutes
                  else "the observed aggregate was not resolved reliably")
        warnings.append(
            f"{reason}; the task-derived total ({model_total:.1f} min) is used instead "
            f"and remains an estimate, not an observation")
        return TimeReconciliation(
            model_total_minutes=round(model_total, 2), shares=shares,
            reconciled_times=[round(t or 0.0, 3) for t in task_times],
            authoritative_total_minutes=round(model_total, 2),
            total_provenance=Provenance.ESTIMATED,
            statement=f"task-derived total used ({reason})", warnings=warnings)

    divergence = abs(model_total - observed_total_minutes) / observed_total_minutes
    severity = severity_for(divergence)
    reconciled = [observed_total_minutes * s for s in shares]

    if severity == DivergenceSeverity.NONE:
        statement = (f"task decomposition ({model_total:.1f} min) agrees with the "
                     f"observed handling time ({observed_total_minutes:.1f} min) within "
                     f"{DIVERGENCE_MODERATE:.0%}")
    elif severity == DivergenceSeverity.SEVERE:
        statement = (f"task decomposition totals {model_total:.1f} min against an "
                     f"observed {observed_total_minutes:.1f} min — a {divergence:.0%} "
                     f"divergence. These are not two views of the same process; "
                     f"reconciliation is required before the split can be used.")
    else:
        statement = (f"task decomposition totals {model_total:.1f} min against an "
                     f"observed {observed_total_minutes:.1f} min ({divergence:.0%} "
                     f"divergence). The observed total is authoritative, so task times "
                     f"were rescaled to sum to it; the decomposition supplies "
                     f"proportions only.")

    return TimeReconciliation(
        user_total_minutes=observed_total_minutes,
        model_total_minutes=round(model_total, 2), divergence=round(divergence, 4),
        severity=severity, reconciled=severity != DivergenceSeverity.SEVERE,
        reconciled_times=[round(r, 3) for r in reconciled], shares=shares,
        authoritative_total_minutes=observed_total_minutes,
        total_provenance=Provenance.USER_PROVIDED,
        statement=statement, warnings=warnings)
