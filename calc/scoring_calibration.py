"""Scoring calibration registry — spec section 27.

Every threshold, weight and ladder the scoring layer uses lives here, with a
parameter_id, unit, provenance, rationale and version. The scoring system
contains no scattered magic numbers.

None of these is an empirical measurement. They are product-design
calibrations, and they are labelled as such wherever they surface. The purpose
is auditability, not the appearance of rigour.
"""

from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, Field

from schemas.assessment_state import ImpactSeverity, Provenance

SCORING_CALIBRATION_VERSION = 1
LAST_REVIEWED = "2026-08-19"


class ScoringParam(BaseModel):
    parameter_id: str
    value: float
    unit: str
    rationale: str
    provenance: Provenance = Provenance.ASSUMED
    version: int = SCORING_CALIBRATION_VERSION
    last_reviewed: str = LAST_REVIEWED


def _p(pid: str, value: float, unit: str, rationale: str) -> ScoringParam:
    return ScoringParam(parameter_id=pid, value=value, unit=unit, rationale=rationale)


# --- Economic score normalisation (spec 18) --------------------------------
ECONOMIC = {
    "payback_full_score_months": _p(
        "economic.payback_full_score_months", 6.0, "months",
        "a build recouped within two quarters is unambiguously attractive on "
        "payback alone"),
    "payback_zero_score_months": _p(
        "economic.payback_zero_score_months", 24.0, "months",
        "beyond two years the payback argument stops carrying the case on its own"),
    "benefit_cost_saturation": _p(
        "economic.benefit_cost_saturation", 2.0, "ratio",
        "returning twice the first-year cost saturates this component; beyond "
        "that the distinction stops being decision-relevant"),
    "weight_payback": _p(
        "economic.weight_payback", 0.60, "weight",
        "how fast cost is recouped is weighted above raw ratio for an MVP"),
    "weight_benefit_cost": _p(
        "economic.weight_benefit_cost", 0.40, "weight",
        "complement of the payback weight"),
}

# --- Economic sanity gate (S3) ---------------------------------------------
SANITY = {
    "implausible_payback_months": _p(
        "sanity.implausible_payback_months", 3.0, "months",
        "a payback under one quarter usually means the baseline is overstated "
        "or the implementation cost is understated, not that the deal is "
        "exceptional"),
    "extreme_benefit_cost_ratio": _p(
        "sanity.extreme_benefit_cost_ratio", 10.0, "ratio",
        "returning ten times the implementation cost in year one is more often "
        "a modelling artefact than a finding"),
}

# --- Feasibility weights (spec 19) -----------------------------------------
FEASIBILITY = {
    "weight_data_readiness": _p(
        "feasibility.weight_data_readiness", 0.45, "weight",
        "weighted heaviest because unsupported data is the most commonly cited "
        "cause of abandonment (Gartner prediction, Feb 2025) — the prediction "
        "justifies the WEIGHT, not the score"),
    "weight_achievability": _p(
        "feasibility.weight_achievability", 0.30, "weight",
        "how plausible the automation estimate is"),
    "weight_integration": _p(
        "feasibility.weight_integration", 0.25, "weight",
        "integration complexity as a delivery constraint"),
    "width_penalty": _p(
        "feasibility.width_penalty", 30.0, "score points per unit relative width",
        "a wide automation estimate is a weaker basis for a build decision than "
        "a tight one at the same midpoint"),
}

DATA_READINESS_SCORES = {
    "none": _p("feasibility.readiness.none", 0.0, "score", "no usable data exists"),
    "minimal": _p("feasibility.readiness.minimal", 25.0, "score", "substantial preparation needed"),
    "partial": _p("feasibility.readiness.partial", 50.0, "score", "usable but incomplete"),
    "good": _p("feasibility.readiness.good", 75.0, "score", "light cleaning only"),
    "excellent": _p("feasibility.readiness.excellent", 100.0, "score", "usable as-is"),
}

INTEGRATION_SCORES = {
    "small": _p("feasibility.integration.small", 100.0, "score", "few systems, standard interfaces"),
    "medium": _p("feasibility.integration.medium", 60.0, "score", "several systems or custom work"),
    "large": _p("feasibility.integration.large", 25.0, "score", "many systems or bespoke integration"),
}

# --- Risk calibration (S5, S6, S7) -----------------------------------------
IMPACT_SEVERITY_WEIGHTS = {
    ImpactSeverity.NEGLIGIBLE.value: _p(
        "risk.severity.negligible", 0.10, "weight 0-1",
        "MVP calibration: a wrong output is noticed and corrected at no real cost"),
    ImpactSeverity.MINOR.value: _p(
        "risk.severity.minor", 0.30, "weight 0-1",
        "MVP calibration: rework, no external consequence"),
    ImpactSeverity.MODERATE.value: _p(
        "risk.severity.moderate", 0.50, "weight 0-1",
        "MVP calibration: customer-visible or financially material once"),
    ImpactSeverity.MAJOR.value: _p(
        "risk.severity.major", 0.75, "weight 0-1",
        "MVP calibration: significant financial or reputational consequence"),
    ImpactSeverity.SEVERE.value: _p(
        "risk.severity.severe", 1.00, "weight 0-1",
        "MVP calibration: regulatory, safety or existential consequence"),
}

# S5: the reliability gap is a MODIFIER on base risk, not a replacement for
# failure probability. Expressed as a categorical band because no defensible
# continuous calibration exists.
RELIABILITY_MODIFIER_BANDS = [
    (0.02, _p("risk.reliability.negligible", 1.00, "multiplier on base risk",
              "a shortfall under 2 points is within the noise of the accuracy "
              "estimate itself")),
    (0.05, _p("risk.reliability.small", 1.15, "multiplier on base risk",
              "MVP assumption: a small shortfall raises exposure modestly")),
    (0.15, _p("risk.reliability.moderate", 1.40, "multiplier on base risk",
              "MVP assumption")),
    (1.00, _p("risk.reliability.large", 1.80, "multiplier on base risk",
              "MVP assumption: a large shortfall means the solution does not "
              "meet the stated bar")),
]

# S7: what fraction of raw errors ESCAPE the review step, per HITL mode.
# Deliberately ranges, and deliberately not a universal "review catches 90%".
RESIDUAL_ESCAPE_FRACTION = {
    "autonomous": (1.0, 1.0, "no human check: every raw error reaches the outcome"),
    "escalation": (0.55, 0.85, "MVP assumption: only escalated items are seen, so "
                               "most errors on non-escalated items still escape"),
    "human_review": (0.15, 0.50, "MVP assumption: review catches a substantial but "
                                 "unmeasured share; the wide band reflects that "
                                 "reviewer effectiveness is not evidenced here"),
    "ai_assisted": (0.20, 0.60, "MVP assumption: the worker remains the author, but "
                                "assistance can introduce errors they do not catch"),
    "human_only": (0.0, 0.0, "no AI output to fail"),
}

# --- Composite (S8) ---------------------------------------------------------
COMPOSITE = {
    "weight_economic": _p("composite.weight_economic", 0.40, "weight",
                          "product-design weighting, not an empirical truth"),
    "weight_feasibility": _p("composite.weight_feasibility", 0.30, "weight",
                             "product-design weighting"),
    "weight_risk": _p("composite.weight_risk", 0.30, "weight",
                      "product-design weighting"),
}

# --- Driver impact blending (spec section 3) -------------------------------
DRIVER_IMPACT_WEIGHTS = {
    "annual_benefit": _p("driver.weight_annual_benefit", 0.40, "weight",
                         "the recurring quantity most decisions turn on"),
    "first_year_net_benefit": _p("driver.weight_net_benefit", 0.35, "weight",
                                 "captures the implementation cost the recurring "
                                 "figure ignores"),
    "payback": _p("driver.weight_payback", 0.25, "weight",
                  "weighted lowest because it is undefined in legitimate cases"),
}


def value(param: ScoringParam) -> float:
    return param.value


def reliability_modifier(gap: Optional[float]) -> tuple[float, ScoringParam]:
    """Categorical reliability modifier (S5). Returns (multiplier, parameter)."""
    if gap is None or gap <= 0:
        return 1.0, RELIABILITY_MODIFIER_BANDS[0][1]
    for threshold, param in RELIABILITY_MODIFIER_BANDS:
        if gap <= threshold:
            return param.value, param
    return RELIABILITY_MODIFIER_BANDS[-1][1].value, RELIABILITY_MODIFIER_BANDS[-1][1]


def escape_fraction(hitl_value: str) -> tuple[float, float, str]:
    """S7: the fraction of raw errors that escape review, per HITL mode."""
    return RESIDUAL_ESCAPE_FRACTION.get(
        hitl_value, RESIDUAL_ESCAPE_FRACTION["human_review"])


def audit_table() -> list[dict]:
    """Every scoring calibration parameter, with the fields section 27 requires."""
    groups = [ECONOMIC, SANITY, FEASIBILITY, DATA_READINESS_SCORES,
              INTEGRATION_SCORES, IMPACT_SEVERITY_WEIGHTS, COMPOSITE,
              DRIVER_IMPACT_WEIGHTS]
    rows = []
    for g in groups:
        for p in g.values():
            rows.append(p.model_dump(mode="json"))
    for _, p in RELIABILITY_MODIFIER_BANDS:
        rows.append(p.model_dump(mode="json"))
    for mode, (lo, hi, why) in RESIDUAL_ESCAPE_FRACTION.items():
        rows.append({"parameter_id": f"risk.escape_fraction.{mode}",
                     "value": (lo + hi) / 2, "unit": "fraction of raw errors escaping",
                     "rationale": why, "provenance": Provenance.ASSUMED.value,
                     "version": SCORING_CALIBRATION_VERSION,
                     "last_reviewed": LAST_REVIEWED, "min": lo, "max": hi})
    return rows


DISCLOSURE = (
    f"scoring calibration v{SCORING_CALIBRATION_VERSION}: all thresholds, "
    f"weights and ladders are explicit product calibrations, not empirical "
    f"measurements — see calc/scoring_calibration.py")
