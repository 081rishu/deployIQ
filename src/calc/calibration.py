"""Economic Engine calibration — versioned, visible, assumption-tagged.

Every constant the engine used to hide (20% review, 15% maintenance, the
15/20/25/15/10/5/10 stage split) lives here as an explicit RANGE with a
rationale and a version. None of them is empirical industry data, and none of
them may be presented as such.

Each is exposed to sensitivity analysis, because a consequential assumption
that cannot be varied is indistinguishable from a fact.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from schemas.assessment_state import Provenance, RangeEstimate

CALIBRATION_VERSION = 1


LAST_REVIEWED = "2026-08-19"


class CalibratedRange(BaseModel):
    """One calibration parameter, fully auditable (spec section 9)."""
    calibration_id: str
    min: float
    max: float
    unit: str
    rationale: str
    provenance: Provenance = Provenance.ASSUMED
    version: int = CALIBRATION_VERSION
    last_reviewed: str = LAST_REVIEWED

    @property
    def key(self) -> str:            # backwards-compatible accessor
        return self.calibration_id

    def as_range(self) -> RangeEstimate:
        return RangeEstimate(
            min=self.min, max=self.max, confidence="low", provenance=self.provenance,
            source=(f"calibration v{self.version} [{self.calibration_id}], "
                    f"reviewed {self.last_reviewed}: {self.rationale}"))

    @property
    def mid(self) -> float:
        return (self.min + self.max) / 2.0


# E3.1 — review effort as a share of full handling time, per HITL mode.
# Used only where the architecture does not describe review directly.
REVIEW_FRACTION_BY_HITL: dict[str, CalibratedRange] = {
    "human_review": CalibratedRange(
        calibration_id="review_fraction.human_review", unit="fraction of full handling time", min=0.10, max=0.40,
        rationale="checking a produced item against a source costs a fraction of "
                  "producing it; the spread reflects how much of the item a "
                  "reviewer must actually re-derive"),
    "escalation": CalibratedRange(
        calibration_id="review_fraction.escalation", unit="fraction of full handling time", min=0.60, max=1.00,
        rationale="an escalated item is largely re-handled by a person, so it "
                  "costs most or all of the original handling time"),
    "ai_assisted": CalibratedRange(
        calibration_id="review_fraction.ai_assisted", unit="fraction of full handling time", min=0.0, max=0.0,
        rationale="the worker is already in the loop; assistance is modelled as a "
                  "throughput uplift, not as a separate review step"),
    "autonomous": CalibratedRange(
        calibration_id="review_fraction.autonomous", unit="fraction of full handling time", min=0.0, max=0.0,
        rationale="no human check by definition"),
    "human_only": CalibratedRange(
        calibration_id="review_fraction.human_only", unit="fraction of full handling time", min=0.0, max=0.0,
        rationale="no AI output to review"),
}

# E3.2 — annual maintenance as a share of build effort, now a range.
MAINTENANCE_FRACTION = CalibratedRange(
    calibration_id="maintenance_fraction", unit="fraction of build effort per year", min=0.10, max=0.25,
    rationale="ongoing model, prompt and integration upkeep as a share of the "
              "original build; NOT an industry constant")

# Section 9 — implementation stage allocation. Partitions the estimator's
# effort band; it never creates a second effort estimate.
STAGE_ALLOCATION: dict[str, CalibratedRange] = {
    "data_preparation": CalibratedRange(
        calibration_id="stage.data_preparation", unit="share of total build effort", min=0.10, max=0.20,
        rationale="MVP calibration: gathering, labelling and shaping inputs"),
    "model_selection": CalibratedRange(
        calibration_id="stage.model_selection", unit="share of total build effort", min=0.15, max=0.25,
        rationale="MVP calibration: model choice, prompting and tuning"),
    "integration": CalibratedRange(
        calibration_id="stage.integration", unit="share of total build effort", min=0.20, max=0.30,
        rationale="MVP calibration: usually the largest single stage"),
    "testing_qa": CalibratedRange(
        calibration_id="stage.testing_qa", unit="share of total build effort", min=0.10, max=0.20,
        rationale="MVP calibration: evaluation harness and acceptance testing"),
    "deployment": CalibratedRange(
        calibration_id="stage.deployment", unit="share of total build effort", min=0.05, max=0.15,
        rationale="MVP calibration: release and cutover"),
    "monitoring_setup": CalibratedRange(
        calibration_id="stage.monitoring_setup", unit="share of total build effort", min=0.03, max=0.08,
        rationale="MVP calibration: dashboards, alerts and drift checks"),
}

def stage_partition() -> dict[str, float]:
    """Normalised stage shares that sum to exactly 1.0.

    Section 10: the estimator's effort band is the authoritative TOTAL. This
    module only partitions it. The declared calibration midpoints sum to less
    than 1, so they are normalised — otherwise the partition would quietly
    lose a slice of the build.
    """
    mids = {k: v.mid for k, v in STAGE_ALLOCATION.items()}
    total = sum(mids.values())
    return {k: v / total for k, v in mids.items()}


def audit_table() -> list[dict]:
    """Every calibration parameter with the fields section 9 requires."""
    rows = []
    for p in all_params():
        rows.append({"calibration_id": p.calibration_id, "min": p.min, "max": p.max,
                     "unit": p.unit, "provenance": p.provenance.value,
                     "rationale": p.rationale, "version": p.version,
                     "last_reviewed": p.last_reviewed})
    # The employer-load multiplier lives with the labor data, not here, but it
    # is the same KIND of assumption and must appear in the same audit surface.
    from lib.labor_rates import load_rates
    m = load_rates().fully_loaded_multiplier
    rows.append({"calibration_id": "employer_load_multiplier", "min": m.min,
                 "max": m.max, "unit": "multiplier on market compensation",
                 "provenance": m.provenance, "rationale": m.rationale,
                 "version": load_rates().version, "last_reviewed": "2026-08-19",
                 "status": m.status})
    return rows


DISCLOSURE = (
    f"economic calibration v{CALIBRATION_VERSION}: review, maintenance and stage "
    f"allocations are explicit MVP assumptions with stated ranges, not measured "
    f"industry values — see calc/calibration.py"
)


def all_params() -> list[CalibratedRange]:
    out = list(REVIEW_FRACTION_BY_HITL.values()) + [MAINTENANCE_FRACTION]
    out.extend(STAGE_ALLOCATION.values())
    return out


def review_fraction_for(hitl_value: str) -> CalibratedRange:
    return REVIEW_FRACTION_BY_HITL.get(
        hitl_value, REVIEW_FRACTION_BY_HITL["human_review"])
