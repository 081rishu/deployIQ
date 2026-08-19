"""Reliability gap as an economic consequence — E11.

The risk score already computes `required accuracy - expected accuracy`. This
module translates that gap into operational cost ONLY where a defensible
consequence exists, and leaves it ABSENT otherwise.

No risk score is computed here. A reliability gap with no costable consequence
stays a qualitative risk owned by the risk module — inventing a dollar figure
for it would be exactly the kind of false precision the product exists to
avoid.
"""

from __future__ import annotations

from typing import Optional

from pydantic import BaseModel

from calc.models import CostLine, midpoint, money, mul, scale
from schemas.assessment_state import point, AssessmentState, Provenance, RangeEstimate


class ReliabilityConsequence(BaseModel):
    gap: Optional[float] = None            # fraction, positive only
    costable: bool = False
    line: Optional[CostLine] = None
    statement: str = ""


def consequence(
    state: AssessmentState,
    required_accuracy: Optional[RangeEstimate],
    expected_accuracy: Optional[RangeEstimate],
    annual_volume: Optional[RangeEstimate],
    hourly_rate: Optional[RangeEstimate],
) -> ReliabilityConsequence:
    """Cost of the extra handling a reliability shortfall implies."""
    if required_accuracy is None or expected_accuracy is None:
        return ReliabilityConsequence(
            statement="no reliability gap computable: required or expected "
                      "accuracy is unavailable")

    gap = midpoint(required_accuracy) - midpoint(expected_accuracy)
    if gap <= 0:
        return ReliabilityConsequence(
            gap=0.0, statement="expected accuracy meets the required bar; no "
                               "additional handling is implied")

    # A gap is only costable when we know what handling a failure requires.
    rework_minutes = point(state.rework_time_per_error_minutes)
    if rework_minutes is None or annual_volume is None or hourly_rate is None:
        return ReliabilityConsequence(
            gap=gap, costable=False,
            line=CostLine.absent(
                "reliability_gap", "Reliability-gap handling",
                f"expected accuracy falls {gap:.1%} short of the required bar, but "
                f"no rework time per failure was provided, so the operational cost "
                f"cannot be estimated. This remains a qualitative risk rather than "
                f"a fabricated figure."),
            statement=(f"reliability gap of {gap:.1%} identified but not costable — "
                       f"it stays with the risk module"))

    extra_units = scale(annual_volume, gap,
                        source=f"annual volume x reliability gap {gap:.1%}")
    extra_hours = scale(extra_units, float(rework_minutes) / 60.0,
                        source=f"shortfall units x {rework_minutes} min handling")
    cost = mul(extra_hours, hourly_rate, source="shortfall handling hours x labor rate")
    return ReliabilityConsequence(
        gap=gap, costable=True,
        line=CostLine(
            key="reliability_gap", label="Reliability-gap handling",
            amount=cost,
            note=(f"{gap:.1%} of output is expected to fall short of the required "
                  f"bar and to need the same handling as an error")),
        statement=(f"reliability gap of {gap:.1%} costed as additional handling on "
                   f"{midpoint(extra_units):,.0f} units per year"))
