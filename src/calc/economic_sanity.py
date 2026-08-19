"""Economic plausibility gate — S3.

Runs BEFORE score normalisation and changes nothing about the Economic Engine.
It classifies the engine's output so an implausible result cannot be presented
as a strong one.

The response to "this looks too good" is never to cap the score — that swaps
one arbitrary rule for another. It is:

    score + sanity flag + uncertainty
"""

from __future__ import annotations

from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field

from calc.lifecycle import FirstYearEconomics
from calc.models import midpoint
from calc.scoring_calibration import SANITY


class SanityLevel(str, Enum):
    OK = "ok"
    WARNING = "warning"
    INVALID = "invalid"


class EconomicOutcome(str, Enum):
    POSITIVE = "positive"
    NEGATIVE = "negative"
    RANGE_CROSSING = "range_crossing"


class SanityFlag(BaseModel):
    code: str
    level: SanityLevel
    statement: str
    parameter_id: Optional[str] = None


class EconomicSanity(BaseModel):
    level: SanityLevel = SanityLevel.OK
    outcome: EconomicOutcome = EconomicOutcome.POSITIVE
    flags: list[SanityFlag] = Field(default_factory=list)

    @property
    def presentable_as_strong(self) -> bool:
        """Whether a high Economic Score may be presented as solid evidence."""
        return self.level == SanityLevel.OK


def assess(fy: FirstYearEconomics) -> EconomicSanity:
    flags: list[SanityFlag] = []

    savings = fy.annual_cost_savings
    net = fy.first_year_net_benefit

    # D — range crossing
    if savings.min < 0 < savings.max:
        outcome = EconomicOutcome.RANGE_CROSSING
        flags.append(SanityFlag(
            code="range_crossing", level=SanityLevel.WARNING,
            statement=(f"annual savings span {savings.min:,.0f} to {savings.max:,.0f} "
                       f"— the assumptions admit both a clearly positive and a "
                       f"clearly negative outcome")))
    elif midpoint(savings) < 0:
        outcome = EconomicOutcome.NEGATIVE
    else:
        outcome = EconomicOutcome.POSITIVE

    # A — implausibly short payback
    floor = SANITY["implausible_payback_months"]
    if fy.payback_months is not None and fy.payback_months.max < floor.value:
        flags.append(SanityFlag(
            code="implausible_payback", level=SanityLevel.WARNING,
            parameter_id=floor.parameter_id,
            statement=(f"payback of {fy.payback_months.min:.1f}-"
                       f"{fy.payback_months.max:.1f} months is below the "
                       f"{floor.value:g}-month plausibility floor. That usually "
                       f"means the current-cost baseline is overstated or the "
                       f"implementation cost understated, rather than that the "
                       f"case is exceptional.")))

    # B — extreme benefit/cost ratio
    impl = midpoint(fy.implementation_cost)
    if impl > 0:
        ratio = midpoint(savings) / impl
        cap = SANITY["extreme_benefit_cost_ratio"]
        if ratio > cap.value:
            flags.append(SanityFlag(
                code="extreme_benefit_cost", level=SanityLevel.WARNING,
                parameter_id=cap.parameter_id,
                statement=(f"annual savings are {ratio:.1f}x the implementation "
                           f"cost, above the {cap.value:g}x plausibility "
                           f"threshold — more often a modelling artefact than a "
                           f"finding")))

    # C — contradictory economics
    if fy.payback_months is not None and midpoint(savings) <= 0:
        flags.append(SanityFlag(
            code="contradictory_payback", level=SanityLevel.INVALID,
            statement=("a payback figure exists while annual savings are not "
                       "positive — these cannot both hold")))
    if fy.payback_months is not None and midpoint(net) < 0 and midpoint(savings) <= 0:
        flags.append(SanityFlag(
            code="contradictory_net_benefit", level=SanityLevel.INVALID,
            statement="a finite positive payback with negative net benefit is an "
                      "invalid state"))

    level = SanityLevel.OK
    if any(f.level == SanityLevel.INVALID for f in flags):
        level = SanityLevel.INVALID
    elif flags:
        level = SanityLevel.WARNING
    return EconomicSanity(level=level, outcome=outcome, flags=flags)
