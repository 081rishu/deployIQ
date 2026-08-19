"""Deterministic workload shares and time reconciliation — C5, N3, N4 (D3).

The LLM must not decide that a task is 60% of the workload. It may estimate
how long each task takes; code turns those durations into shares:

    workload_i       = volume_i x handling_time_i
    workload_share_i = workload_i / sum(workload)

Every unit flows through each task in a single process, so volume is common
and handling time is what differentiates them.

D3 — WHEN THE TWO SOURCES DISAGREE:
The user's observed aggregate handling time is authoritative when it is
resolved and reliable. The LLM's decomposition supplies only RELATIVE
allocation. So the model's task times are normalised into proportions and the
observed total is allocated across them:

    reconciled_time_i = user_total x (task_time_i / model_total)

The divergence between model_total and user_total is recorded, and a large
divergence lowers confidence rather than being silently normalised away. This
policy is shared with the Economic Engine so the two modules cannot derive
different labor baselines from the same assessment.
"""

from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, Field

# D3 lives in ONE place, shared with the Economic Engine (finesse spec 2).
from lib.reconciliation import (
    DivergenceSeverity,
    TimeReconciliation,
    reconcile,
)
from schemas.assessment_state import Provenance, RangeEstimate

MIN_SHARE_SUM = 1e-9


class WorkloadSplit(BaseModel):
    shares: list[float] = Field(default_factory=list)
    provenance: Provenance = Provenance.DERIVED
    basis: str = ""
    warning: Optional[str] = None
    reconciliation: TimeReconciliation = Field(default_factory=TimeReconciliation)


def _midpoints(times: list[Optional[RangeEstimate]]) -> list[Optional[float]]:
    return [None if t is None else (t.min + t.max) / 2.0 for t in times]


def _reconcile(times, names, user_total_minutes) -> TimeReconciliation:
    return reconcile(_midpoints(times), names, user_total_minutes)


def derive_shares(
    handling_times: list[Optional[RangeEstimate]], task_names: list[str],
    user_total_minutes: Optional[float] = None,
) -> WorkloadSplit:
    """Shares from per-task handling time, reconciled against the observed
    total where one exists (D3)."""
    if not handling_times:
        return WorkloadSplit(shares=[], provenance=Provenance.ASSUMED,
                             basis="no tasks", warning="no tasks to weight")

    missing = [name for name, t in zip(task_names, handling_times) if t is None]
    if missing:
        n = len(handling_times)
        return WorkloadSplit(
            shares=[1.0 / n] * n, provenance=Provenance.ASSUMED,
            basis=f"equal split across {n} tasks",
            warning=(f"per-task handling time missing for {missing}, so workload "
                     f"shares are an explicit equal split, NOT derived from "
                     f"volume x handling time. Task-level economics built on this "
                     f"are proportional guesses."),
            reconciliation=_reconcile(handling_times, task_names, user_total_minutes))

    reconciliation = _reconcile(handling_times, task_names, user_total_minutes)
    weights = [(t.min + t.max) / 2.0 for t in handling_times]  # type: ignore[union-attr]
    total = sum(weights)
    if total <= MIN_SHARE_SUM:
        n = len(handling_times)
        return WorkloadSplit(shares=[1.0 / n] * n, provenance=Provenance.ASSUMED,
                             basis="equal split (handling times summed to zero)",
                             warning="handling times summed to zero",
                             reconciliation=reconciliation)

    basis = ("workload share = task handling time / total handling time "
             "(volume is common to all tasks in one process)")
    if reconciliation.severity not in (DivergenceSeverity.NONE,):
        basis += f". {reconciliation.statement}"
    return WorkloadSplit(shares=[w / total for w in weights],
                         provenance=Provenance.DERIVED, basis=basis,
                         warning=(reconciliation.statement
                                  if reconciliation.severity != DivergenceSeverity.NONE
                                  else None),
                         reconciliation=reconciliation)
