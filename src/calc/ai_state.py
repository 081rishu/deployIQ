"""AI-state task economics and annual operating cost — spec 8.3, 8.4.

The distinction this module exists to enforce:

    Automation != Productivity Improvement
              != Labor Hours Eliminated
              != Headcount Reduction

Two separate mechanisms keep those apart:

1. HITL mode decides how a task's automation converts to residual work. An
   AI-ASSISTED task is modelled as a productivity uplift (the worker stays,
   throughput rises) and can never reach zero labor, which is the whole point
   of the Brynjolfsson/Li/Raymond finding cited in spec 8.3 — 5,179 agents,
   14% more issues resolved per hour, not 14% fewer agents.

2. LaborRealization decides whether freed hours become money. Reduced workload
   only lowers spend if the organisation actually removes the cost; otherwise
   it is capacity, and is reported as capacity. The engine requires this to be
   stated explicitly rather than defaulted silently (spec 8.4).

Every conversion factor below is an ASSUMPTION, named and tunable in one place
rather than scattered through the arithmetic.
"""

from __future__ import annotations

from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field

from calc import calibration
from calc.models import (
    CostBreakdown,
    CostLine,
    add,
    complement,
    midpoint,
    money,
    mul,
    scale,
    sub,
)
from lib.benchmarks import figure as benchmark_figure
from schemas.assessment_state import Provenance, RangeEstimate, Sector
from solution.schema import HitlMode, SolutionEstimate

# --- Modelling assumptions (spec 8.3). All ASSUMED provenance. -------------

# E3: review effort is NOT one global constant. It comes from the task's HITL
# mode via calc/calibration.py, as an explicit RANGE with a rationale, and is
# exposed to sensitivity. A universal 20% was too consequential to hide — the
# sweep showed it moving first-year net benefit more than implementation cost.
REVIEW_TIME_FRACTION = 0.20   # retained only as the sensitivity sweep's midpoint

# An AI-assisted task's automation figure is read as a throughput uplift:
# residual time per unit = 1 / (1 + uplift). Capped so an implausible LLM
# estimate cannot drive assisted labor toward zero.
MAX_ASSIST_UPLIFT = 1.0

# Per-unit inference price keys in the sector packs.
_INFERENCE_KEYS = {
    Sector.DOCUMENT_PROCESSING: "invoice_extraction_price_per_page",
    Sector.CUSTOMER_SUPPORT: None,      # no sourced per-ticket inference price
}


class LaborRealization(str, Enum):
    """Spec 8.4 — what happens to freed capacity. Never defaulted silently."""
    COST_ELIMINATED = "cost_eliminated"      # workload reduction is taken as spend reduction
    CAPACITY_RETAINED = "capacity_retained"  # staff retained; freed time is capacity, not savings


class TaskEconomics(BaseModel):
    task: str
    hitl: HitlMode
    workload_share: float
    current_cost: RangeEstimate
    automation: RangeEstimate            # fraction 0-1
    residual_work_fraction: RangeEstimate
    modelled_residual_labor: RangeEstimate   # cost if freed work were removed
    realized_residual_labor: RangeEstimate   # cost under the realization policy
    human_review_cost: Optional[RangeEstimate] = None
    freed_capacity_value: RangeEstimate      # labor value freed but not banked
    mechanism: str = ""


def _automation_fraction(est: RangeEstimate) -> RangeEstimate:
    """Estimator reports automation as a percentage; economics needs 0-1."""
    return RangeEstimate(
        min=max(0.0, min(est.min, 100.0)) / 100.0,
        max=max(0.0, min(est.max, 100.0)) / 100.0,
        confidence=est.confidence, provenance=est.provenance,
        source=est.source or "task automation estimate",
    )


def residual_work_fraction(hitl: HitlMode, automation: RangeEstimate) -> tuple[RangeEstimate, str]:
    """Convert automation to residual human work, per HITL mode (spec 8.3)."""
    if hitl == HitlMode.HUMAN_ONLY:
        return (money(1.0, provenance=Provenance.ASSUMED, confidence="high",
                      source="human-only task: no labor displaced"),
                "human-only — no labor displaced")

    if hitl == HitlMode.AI_ASSISTED:
        # Productivity framing: throughput rises, the worker remains.
        lo = min(automation.min, MAX_ASSIST_UPLIFT)
        hi = min(automation.max, MAX_ASSIST_UPLIFT)
        return (RangeEstimate(
            min=1.0 / (1.0 + hi), max=1.0 / (1.0 + lo),
            confidence=automation.confidence, provenance=Provenance.ASSUMED,
            source=("assisted task modelled as throughput uplift: residual time "
                    "per unit = 1/(1+uplift); labor is reduced, not removed"),
        ), "AI-assisted — modelled as productivity uplift, not work removal")

    # AUTONOMOUS / HUMAN_REVIEW / ESCALATION: the AI performs the share it
    # automates; humans retain the remainder. Review is costed separately.
    return (complement(automation,
                       source="residual human work = 1 - automated share"),
            {
                HitlMode.AUTONOMOUS: "autonomous — automated share removed from human workload",
                HitlMode.HUMAN_REVIEW: "AI performs the work; every AI output is reviewed (costed separately)",
                HitlMode.ESCALATION: "AI handles routine share; humans handle escalations",
            }[hitl])


def task_economics(
    task_name: str, hitl: HitlMode, workload_share: float,
    current_cost: RangeEstimate, automation_pct: RangeEstimate,
    realization: LaborRealization,
    review_fraction_override: Optional[float] = None,
) -> TaskEconomics:
    automation = _automation_fraction(automation_pct)
    residual_fraction, mechanism = residual_work_fraction(hitl, automation)

    modelled = mul(current_cost, residual_fraction,
                   source="current task cost x residual work fraction")
    freed = sub(current_cost, modelled, source="labor value freed by the AI scenario")

    review = None
    calibrated = calibration.review_fraction_for(hitl.value)
    if calibrated.max > 0:
        automated_cost = mul(current_cost, automation,
                             source="current task cost x automated share")
        if review_fraction_override is not None:
            review = scale(automated_cost, review_fraction_override,
                           source=(f"review at {review_fraction_override:.0%} of full "
                                   f"handling time (sensitivity override)"))
        else:
            # A range, not a point: the low bound is a quick check, the high
            # bound is a reviewer re-deriving most of the item.
            review = mul(automated_cost, calibrated.as_range(),
                         source=(f"review of AI output at "
                                 f"{calibrated.min:.0%}-{calibrated.max:.0%} of full "
                                 f"handling time for HITL mode '{hitl.value}' "
                                 f"[{calibrated.key}]"))

    if realization == LaborRealization.COST_ELIMINATED:
        realized = modelled
    else:
        # Staff retained: the labor line does not fall. Freed time is capacity.
        realized = RangeEstimate(
            min=current_cost.min, max=current_cost.max,
            confidence=current_cost.confidence, provenance=Provenance.DERIVED,
            source=("capacity retained: workload falls but headcount cost does "
                    "not, so labor spend is unchanged"),
        )

    return TaskEconomics(
        task=task_name, hitl=hitl, workload_share=workload_share,
        current_cost=current_cost, automation=automation,
        residual_work_fraction=residual_fraction, modelled_residual_labor=modelled,
        realized_residual_labor=realized, human_review_cost=review,
        freed_capacity_value=freed, mechanism=mechanism,
    )


def build_tasks(
    solution: SolutionEstimate, current_labor_cost: RangeEstimate,
    realization: LaborRealization,
    review_fraction_override: Optional[float] = None,
    automation_scale: float = 1.0,
) -> list[TaskEconomics]:
    """Split the current labor cost across the estimator's tasks by workload
    share, then run each through the AI scenario.

    Shares are normalised so they sum to 1. If the estimator supplied shares
    that do not sum to 1 the normalisation is what makes the split coherent —
    but it also silently rescales a bad input, so the caller should check
    `share_warning()` and report it.
    """
    tasks = solution.task_automation
    if not tasks:
        return []
    total_share = sum(max(t.workload_share, 0.0) for t in tasks)
    if total_share <= 0:
        # The estimator now defaults an underived share to 0 rather than 1
        # (C5), so an unset set of shares would otherwise silently zero out
        # every task cost. Fall back to an explicit equal split instead.
        equal = 1.0 / len(tasks)
        tasks = [t.model_copy(update={"workload_share": equal}) for t in tasks]
        total_share = 1.0
    out = []
    for t in tasks:
        share = max(t.workload_share, 0.0) / total_share
        # The automation lever must reach per-task estimates, not just the
        # overall figure — task automation is what drives residual labor, and
        # residual labor is most of the AI operating cost.
        automation = (scale(t.estimate, automation_scale,
                            source=f"{t.estimate.source} (scaled x{automation_scale})")
                      if automation_scale != 1.0 else t.estimate)
        out.append(task_economics(
            task_name=t.task or t.capability.value, hitl=t.hitl, workload_share=share,
            current_cost=scale(current_labor_cost, share,
                               source=f"{share:.0%} of current labor cost"),
            automation_pct=automation, realization=realization,
            review_fraction_override=review_fraction_override,
        ))
    return out


def share_warning(solution: SolutionEstimate) -> Optional[str]:
    """Spec 8.3 relies on workload shares being real. Flag when they are not."""
    tasks = solution.task_automation
    if not tasks:
        return "no task decomposition available — economics fall back to a single blended task"
    total = sum(max(t.workload_share, 0.0) for t in tasks)
    if abs(total - 1.0) > 0.01:
        return (f"task workload shares sum to {total:.2f}, not 1.00 — they were "
                f"normalised to split the labor baseline, so the split is "
                f"proportional but not independently validated")
    return None


def ai_annual_operating_cost(
    tasks: list[TaskEconomics], sector: Sector, annual_volume: Optional[RangeEstimate],
    automation_overall: RangeEstimate, maintenance: Optional[RangeEstimate],
    inference_line: Optional[CostLine] = None,
) -> CostBreakdown:
    """Spec 8.4. Components that were never collected are reported ABSENT
    rather than silently treated as zero."""
    breakdown = CostBreakdown(label="AI annual operating cost")

    residual = add(*[t.realized_residual_labor for t in tasks],
                   source="sum of per-task residual labor")
    breakdown.lines.append(CostLine(
        key="residual_labor", label="Residual labor", amount=residual,
        note="labor still required under the AI scenario"))

    reviews = [t.human_review_cost for t in tasks if t.human_review_cost is not None]
    if reviews:
        breakdown.lines.append(CostLine(
            key="human_review", label="Human review", amount=add(*reviews,
                source="sum of per-task human review"),
            note="review derived per task from its HITL mode; see "
                 "calc/calibration.py for each range and its rationale"))
    else:
        breakdown.lines.append(CostLine.absent(
            "human_review", "Human review",
            "no task is configured for human review of AI output"))

    # E2: inference cost follows the SELECTED architecture (calc/inference.py).
    breakdown.lines.append(inference_line or CostLine.absent(
        "inference", "AI / API inference",
        "no inference cost could be derived for the selected architecture"))

    if maintenance is not None:
        breakdown.lines.append(CostLine(
            key="maintenance", label="Maintenance", amount=maintenance,
            note="ongoing engineering maintenance, derived from build effort"))
    else:
        breakdown.lines.append(CostLine.absent(
            "maintenance", "Maintenance", "no maintenance stage costed"))

    for k, label in (("infrastructure", "AI infrastructure"),
                     ("monitoring", "Monitoring"),
                     ("other_recurring", "Other recurring costs")):
        breakdown.lines.append(CostLine.absent(
            k, label, "not collected by the interviewer"))
    return breakdown


def freed_capacity_total(tasks: list[TaskEconomics]) -> RangeEstimate:
    return add(*[t.freed_capacity_value for t in tasks],
               source="total labor value freed but not removed from spend")
