"""Labor economics — spec 8.1.

Two independent formulations of the same quantity:

  task-level      volume x handling time x hourly rate
  workforce-level workers x fully loaded annual cost x fraction of time

They are NEVER combined or averaged. When both are computable they are run
separately and compared; a material divergence is a finding in its own right,
because it means the user's headcount story and their volume story disagree.
"""

from __future__ import annotations

from enum import Enum
from typing import Optional

from pydantic import BaseModel

from calc.models import (
    HOURS_PER_YEAR,
    MONTHS_PER_YEAR,
    midpoint,
    money,
    mul,
    scale,
)
from lib.benchmarks import figure as benchmark_figure
from lib.labor_rates import LaborKind, fully_loaded, lookup as rate_lookup
from lib.reconciliation import TimeReconciliation, reconcile
from schemas.assessment_state import (
    point,
    AssessmentState,
    FieldResolution,
    Provenance,
    RangeEstimate,
    Sector,
)

# Base-wage pack keys per sector, used only when the user gave no loaded cost.
_WAGE_KEYS = {
    Sector.CUSTOMER_SUPPORT: ("csr_median_hourly_wage", "hourly"),
    Sector.DOCUMENT_PROCESSING: ("ap_clerk_base_annual_wage", "annual"),
}

# How far the two formulations may diverge before it is worth reporting.
DIVERGENCE_TOLERANCE = 0.25   # 25%


class LaborRateStatus(str, Enum):
    RESOLVED = "resolved"
    UNRESOLVED = "unresolved"


class LaborRate(BaseModel):
    hourly: Optional[RangeEstimate] = None
    annual_loaded: Optional[RangeEstimate] = None
    basis: str = ""
    status: LaborRateStatus = LaborRateStatus.RESOLVED
    geography: Optional[str] = None
    currency: Optional[str] = None
    statement: str = ""


class BaselineStatus(str, Enum):
    """E5: how the two labor formulations relate."""
    CONSISTENT = "consistent"
    DIVERGENT = "divergent"
    TASK_DERIVED = "task_derived"        # only the task formulation exists
    WORKFORCE_ONLY = "workforce_only"    # only the workforce formulation exists
    UNRESOLVED = "unresolved"            # neither is defensible


def authoritative_handling_time(
    state: AssessmentState, task_times_minutes: Optional[list[Optional[float]]] = None,
    task_names: Optional[list[str]] = None,
) -> TimeReconciliation:
    """The handling-time baseline, under the SHARED D3 policy.

    The Economic Engine and the Solution Estimator call the same function, so
    the same AssessmentState cannot yield two different labor baselines
    (finesse spec 2, acceptance test N).
    """
    meta = state.field_resolution.get("avg_time_per_unit_minutes")
    reliable = meta is None or meta.status not in (
        FieldResolution.CONTRADICTORY, FieldResolution.AMBIGUOUS)
    return reconcile(
        task_times=task_times_minutes or [],
        task_names=task_names or [],
        observed_total_minutes=point(state.avg_time_per_unit_minutes),
        observed_is_reliable=reliable,
    )


class LaborConsistency(BaseModel):
    """E5: a classification, never a silent choice.

    The workforce formulation is PRIMARY when available: worker count,
    compensation and fraction of time are direct observations about the
    organisation. The task formulation is a validation check and a secondary
    scenario — useful for allocating work, but built on a handling time the
    model may have reconstructed.
    """
    task_based: Optional[RangeEstimate] = None
    workforce_based: Optional[RangeEstimate] = None
    comparable: bool = False
    divergence: Optional[float] = None
    status: BaselineStatus = BaselineStatus.UNRESOLVED
    primary: Optional[RangeEstimate] = None
    secondary: Optional[RangeEstimate] = None
    primary_basis: str = ""
    verdict: str = ""

    @property
    def needs_more_information(self) -> bool:
        return self.status == BaselineStatus.UNRESOLVED


# Geographies for which the sector packs carry wage evidence (US BLS). Other
# geographies resolve through the labor-rate registry instead; anything with
# neither resolves to UNRESOLVED rather than inheriting a foreign rate.
PACK_WAGE_GEOGRAPHIES = {"us", "usa", "united states"}
PACK_WAGE_CURRENCY = "USD"

# The PROCESS role each sector's workers occupy. Implementation labor is a
# separate lookup and must never be served from this map.
SECTOR_PROCESS_ROLE = {
    Sector.CUSTOMER_SUPPORT: "customer_support_agent",
    Sector.DOCUMENT_PROCESSING: "accounts_payable_clerk",
}


def resolve_labor_rate(state: AssessmentState) -> LaborRate:
    """Hourly and annual loaded labor cost for the assessed workers.

    E8 — geography-safe. The sector packs carry US wage data in USD. If the
    assessment is for another geography, or geography is unknown, the rate is
    UNRESOLVED: silently applying US wages to an India-based process would
    corrupt every downstream number while looking perfectly precise.

    A user-provided loaded cost is always usable, because it is the
    organisation's own figure in its own currency.

    Note the packs supply MARKET COMPENSATION, not fully-loaded cost. The
    employer load is a separate, explicitly assumed multiplier, so the two stay
    independently auditable.
    """
    if point(state.fully_loaded_annual_cost):
        annual = money(float(point(state.fully_loaded_annual_cost)),
                       provenance=Provenance.USER_PROVIDED, confidence="high",
                       source="user-provided fully loaded annual cost")
        return LaborRate(
            annual_loaded=annual,
            hourly=scale(annual, 1.0 / HOURS_PER_YEAR,
                         source=f"user annual cost / {HOURS_PER_YEAR}h"),
            basis="user_provided", status=LaborRateStatus.RESOLVED,
            geography=state.geography, currency=None,
            statement="the organisation's own fully-loaded cost was used",
        )

    # Registry first: it carries geographies the packs do not (currently India),
    # with PROCESS labor kept distinct from implementation labor.
    #
    # Fix spec 12: prefer the role the interview actually established. Without
    # this every support assessment was priced at the generalist agent rate,
    # making the specialist rate unreachable and mis-costing a tier-2-heavy
    # process by roughly 2.5x.
    role = SECTOR_PROCESS_ROLE.get(state.sector)
    if state.worker_role_canonical is not None:
        role = state.worker_role_canonical.value
    found = rate_lookup(state.geography, LaborKind.PROCESS, role)
    if found.resolved and found.entry is not None:
        entry = found.entry
        hourly, load_note = fully_loaded(entry)
        if hourly is not None:
            return LaborRate(
                hourly=hourly,
                annual_loaded=scale(hourly, HOURS_PER_YEAR,
                                    source=f"hourly x {HOURS_PER_YEAR}h"),
                basis="rate_registry", status=LaborRateStatus.RESOLVED,
                geography=entry.geography, currency=entry.currency,
                statement=(f"{entry.role} in {entry.geography} from "
                           f"[{entry.rate_id}] ({entry.verification}). {load_note}"))

    geo = (state.geography or "").strip().lower()
    if not geo:
        return LaborRate(
            basis="unresolved", status=LaborRateStatus.UNRESOLVED,
            statement=("no geography on the assessment, and the benchmark packs "
                       "carry US wage data only. A labor rate cannot be resolved "
                       "without either the organisation's own figure or a stated "
                       "geography — defaulting to US would silently mis-cost any "
                       "non-US process."))
    if geo not in PACK_WAGE_GEOGRAPHIES:
        return LaborRate(
            basis="unresolved", status=LaborRateStatus.UNRESOLVED,
            geography=state.geography,
            statement=(f"geography is {state.geography!r}: neither the labor-rate "
                       f"registry nor the sector packs carry {state.geography} "
                       f"process-labor evidence. {found.statement} The rate is "
                       f"unresolved rather than borrowed from another market."))

    key, kind = _WAGE_KEYS[state.sector]
    wage = benchmark_figure(state.sector, key)
    loading = benchmark_figure(state.sector, "fully_loaded_multiplier")
    if wage is None or loading is None:
        return LaborRate(basis="unresolved", status=LaborRateStatus.UNRESOLVED,
                         geography=state.geography,
                         statement="the sector pack has no wage or loading figure")

    base, mult = wage.as_range(), loading.as_range()
    if kind == "hourly":
        hourly = mul(base, mult, source=f"{wage.citation()} x loading {mult.min}-{mult.max}")
        annual = scale(hourly, HOURS_PER_YEAR, source=f"hourly x {HOURS_PER_YEAR}h")
    else:
        annual = mul(base, mult, source=f"{wage.citation()} x loading {mult.min}-{mult.max}")
        hourly = scale(annual, 1.0 / HOURS_PER_YEAR, source=f"annual / {HOURS_PER_YEAR}h")
    return LaborRate(
        hourly=hourly, annual_loaded=annual, basis="benchmark_derived",
        status=LaborRateStatus.RESOLVED, geography="US", currency=PACK_WAGE_CURRENCY,
        statement=("market compensation from the sector pack, lifted by an "
                   "explicitly assumed employer-load multiplier — the two remain "
                   "separately auditable"))


def annual_volume(state: AssessmentState) -> Optional[RangeEstimate]:
    if not point(state.monthly_volume):
        return None
    return scale(money(float(point(state.monthly_volume)),
                       provenance=Provenance.USER_PROVIDED, confidence="high",
                       source="user-provided monthly volume"),
                 MONTHS_PER_YEAR, source="monthly volume x 12")


def task_based_labor_cost(
    volume: RangeEstimate, handling_minutes: RangeEstimate, hourly: RangeEstimate,
) -> RangeEstimate:
    """volume x handling time x hourly rate (spec 8.1)."""
    hours = scale(handling_minutes, 1.0 / 60.0, source="handling minutes / 60")
    return mul(mul(volume, hours, source="volume x hours per unit"), hourly,
               source="volume x handling time x hourly rate")


def workforce_labor_cost(
    workers: float, annual_loaded: RangeEstimate, fraction_time: RangeEstimate,
) -> RangeEstimate:
    """workers x fully loaded annual cost x fraction of time (spec 8.1)."""
    per_worker = mul(annual_loaded, fraction_time,
                     source="loaded annual cost x fraction of time on process")
    return scale(per_worker, workers, source=f"x {workers} workers")


def check_consistency(
    task_based: Optional[RangeEstimate], workforce_based: Optional[RangeEstimate],
) -> LaborConsistency:
    """E5: classify the relationship and pick a PRIMARY on a stated rule.

    The engine used to take the task-based figure whenever both existed. In
    testing the two differed by 51.5% and it silently proceeded on the larger,
    roughly doubling apparent savings. Now: workforce is primary when
    available, the task formulation is the secondary scenario, and a material
    divergence is reported as a finding rather than resolved by arithmetic.
    """
    # Case D — neither is defensible.
    if task_based is None and workforce_based is None:
        return LaborConsistency(
            comparable=False, status=BaselineStatus.UNRESOLVED,
            verdict=("neither labor formulation could be computed. The engine does "
                     "not manufacture a baseline — this needs more information."))

    # Case C — only the task formulation exists.
    if workforce_based is None:
        return LaborConsistency(
            task_based=task_based, comparable=False,
            status=BaselineStatus.TASK_DERIVED, primary=task_based,
            primary_basis="task-based (volume x handling time x rate)",
            verdict=("only the task-based formulation could be computed; no "
                     "headcount cross-check is available, so the baseline rests "
                     "entirely on volume and handling time"))

    # Only the workforce formulation exists.
    if task_based is None:
        return LaborConsistency(
            workforce_based=workforce_based, comparable=False,
            status=BaselineStatus.WORKFORCE_ONLY, primary=workforce_based,
            primary_basis="workforce-based (workers x loaded cost x fraction of time)",
            verdict=("only the workforce-based formulation could be computed; no "
                     "volume cross-check is available"))

    a, b = midpoint(task_based), midpoint(workforce_based)
    if max(a, b) == 0:
        return LaborConsistency(
            task_based=task_based, workforce_based=workforce_based, comparable=True,
            divergence=0.0, status=BaselineStatus.CONSISTENT, primary=workforce_based,
            primary_basis="workforce-based", verdict="both formulations are zero")

    divergence = abs(a - b) / max(a, b)

    # Case A — consistent. Workforce is preferred; task validates it.
    if divergence <= DIVERGENCE_TOLERANCE:
        return LaborConsistency(
            task_based=task_based, workforce_based=workforce_based, comparable=True,
            divergence=divergence, status=BaselineStatus.CONSISTENT,
            primary=workforce_based, secondary=task_based,
            primary_basis="workforce-based (workers x loaded cost x fraction of time)",
            verdict=(f"the two formulations agree within {DIVERGENCE_TOLERANCE:.0%} "
                     f"(gap {divergence:.0%}). The workforce formulation is used as "
                     f"the baseline; the task formulation validates it."))

    # Case B — material divergence. Both scenarios stay inspectable.
    higher = "workforce-based" if b > a else "task-based"
    return LaborConsistency(
        task_based=task_based, workforce_based=workforce_based, comparable=True,
        divergence=divergence, status=BaselineStatus.DIVERGENT,
        primary=workforce_based, secondary=task_based,
        primary_basis="workforce-based (primary under divergence)",
        verdict=(f"the two formulations diverge by {divergence:.0%}: task-based "
                 f"{a:,.0f} vs workforce-based {b:,.0f}, and the {higher} figure is "
                 f"higher. The workforce formulation is used as the primary "
                 f"baseline because worker count, compensation and fraction of time "
                 f"are direct observations about this organisation; the task-based "
                 f"figure is retained as a secondary scenario. They are NOT "
                 f"averaged, and the larger is NOT selected automatically."))
