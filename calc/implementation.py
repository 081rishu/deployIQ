"""Staged implementation cost — spec 8.5.

Engineering effort comes from the estimator's Small/Medium/Large band; this
module never invents hours. The band gives TOTAL build hours, so hours are
distributed across the required build stages using an explicit weight table —
an assumption, stated here rather than buried.

Buy stages need a vendor price the interviewer does not collect, so they are
reported ABSENT. An implementation total that omits buy stages is a floor.
"""

from __future__ import annotations

from typing import Optional

from calc import calibration
from calc.models import CostBreakdown, CostLine, add, money, mul, scale
from schemas.assessment_state import (
    AssessmentState,
    BuyOrBuild,
    EffortBand,
    Provenance,
    RangeEstimate,
)
from solution.effort_bands import hours_for, implementation_rate, labor_rate

# Section 10: stage shares and the maintenance fraction live in
# calc/calibration.py — versioned, unit-bearing and assumption-tagged. This
# module holds NO calibration constants of its own; a second copy here was a
# duplicate calculation path.
STAGE_LABELS = {
    "data_preparation": "Data collection / preparation",
    "model_selection": "Model selection / prompting / fine-tuning",
    "integration": "Integration",
    "testing_qa": "Testing / QA",
    "deployment": "Deployment",
    "monitoring_setup": "Monitoring setup",
    "ongoing_maintenance": "Ongoing maintenance",
}
RECURRING_STAGES = {"ongoing_maintenance"}


# Which stages a platform genuinely supplies rather than the team building
# them. A low-code platform ships deployment, monitoring and much of the
# integration surface; a managed AI service additionally supplies the model
# work. A custom build supplies nothing.
_KIND_SUPPLIES = {
    "low_code": {"deployment", "monitoring_setup"},
    "managed_service": {"deployment", "monitoring_setup", "model_selection"},
    "custom_code": set(),
}


def _stage_plan(state: AssessmentState,
                implementation_kind: Optional[str] = None) -> dict[str, BuyOrBuild]:
    """Buy/build per stage.

    Precedence: what the user actually declared, else what the SELECTED
    implementation supplies, else build.

    Defaulting every unstated stage to BUILD — the most expensive branch —
    meant the spec 8.5 buy-vs-build distinction never engaged no matter which
    architecture was chosen. The platform's own nature is a better default than
    a blanket assumption.
    """
    declared = {s.stage.strip().lower().replace(" ", "_"): s
                for s in state.process_stages if s.required}
    supplied = _KIND_SUPPLIES.get(implementation_kind or "", set())
    plan: dict[str, BuyOrBuild] = {}
    for key in STAGE_LABELS:
        stage = declared.get(key)
        if stage is not None and stage.buy_or_build != BuyOrBuild.UNKNOWN:
            plan[key] = stage.buy_or_build          # the user said so
        elif key in supplied:
            plan[key] = BuyOrBuild.BUY              # the platform supplies it
        else:
            plan[key] = BuyOrBuild.BUILD
    return plan


def implementation_cost(
    state: AssessmentState, band: EffortBand,
    implementation_kind: Optional[str] = None,
) -> tuple[CostBreakdown, Optional[RangeEstimate]]:
    """Return (first-year implementation breakdown, annual maintenance cost).

    Uses IMPLEMENTATION labor for the assessment's geography — engineers
    building the solution, never the process workers being automated.
    """
    hours = hours_for(band)
    rate = labor_rate(state.geography)
    if rate is None:
        b = CostBreakdown(label=f"Implementation cost ({band.value} band)")
        for key in STAGE_LABELS:
            if key in RECURRING_STAGES:
                continue
            b.lines.append(CostLine.absent(
                key, STAGE_LABELS[key],
                f"no engineering labor rate for geography "
                f"{state.geography!r} — not substituted from another market"))
        return b, None
    plan = _stage_plan(state, implementation_kind)
    b = CostBreakdown(label=f"Implementation cost ({band.value} band)")

    partition = calibration.stage_partition()
    build_hours_total = 0.0
    for key, weight in partition.items():
        if key in RECURRING_STAGES:
            continue
        approach = plan[key]
        if approach == BuyOrBuild.BUY:
            b.lines.append(CostLine.absent(
                key, STAGE_LABELS[key],
                "declared buy — vendor/subscription price not collected"))
            continue
        stage_hours = scale(hours, weight, source=f"{weight:.0%} of {band.value} band hours")
        b.lines.append(CostLine(
            key=key, label=STAGE_LABELS[key],
            amount=mul(stage_hours, rate,
                       source=f"{weight:.0%} of band hours x loaded rate "
                              f"({rate.min}-{rate.max}/h)"),
            note=f"build: {stage_hours.min:.0f}-{stage_hours.max:.0f} hrs"))
        build_hours_total += weight

    maintenance = None
    if plan["ongoing_maintenance"] == BuyOrBuild.BUILD:
        frac = calibration.MAINTENANCE_FRACTION
        maint_hours = mul(hours, frac.as_range(),
                          source=f"build hours x maintenance fraction "
                                 f"{frac.min:.0%}-{frac.max:.0%} [{frac.calibration_id}]")
        maintenance = mul(maint_hours, rate,
                          source="annual maintenance hours x engineering rate")
    return b, maintenance
