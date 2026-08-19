"""Scope calibration — N6.

Every weight and threshold in the scope model is an MVP calibration, not an
empirical fact. Keeping them here, each with a rationale and a version, means
they can be identified as assumptions wherever they surface and re-fitted
later against observed project effort.

Guardrail 13: all calibration assumptions must be identifiable as assumptions.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from schemas.assessment_state import DataReadiness, EffortBand, Provenance

CALIBRATION_VERSION = 1


class CalibrationParam(BaseModel):
    key: str                      # parameter_id — unique within this module
    value: float
    unit: str = "scope_points"    # what `value` is measured in
    rationale: str = ""
    provenance: Provenance = Provenance.ASSUMED
    version: int = CALIBRATION_VERSION
    # Empty means NEVER formally reviewed, which is not the same as reviewed
    # and found acceptable. The scope weights below carry no review date
    # because none has been done; nothing is backfilled to look tidier.
    last_reviewed: str = ""

    def __float__(self) -> float:
        return self.value


def _p(key: str, value: float, rationale: str, *,
       unit: str = "scope_points", last_reviewed: str = "") -> CalibrationParam:
    return CalibrationParam(key=key, value=value, rationale=rationale,
                            unit=unit, last_reviewed=last_reviewed)


class ScopeCalibration(BaseModel):
    """All scope weights in one auditable object."""

    points_per_integration: CalibrationParam = _p(
        "points_per_integration", 1.0,
        "each additional system to integrate adds roughly one unit of build "
        "and test work; integrations are the most repeatedly cited effort driver")
    max_integration_points: CalibrationParam = _p(
        "max_integration_points", 5.0,
        "integration effort saturates — the tenth integration is cheaper than "
        "the second because patterns get reused")
    compliance_points_each: CalibrationParam = _p(
        "compliance_points_each", 1.0,
        "each declared constraint adds review, logging and sign-off work")
    max_compliance_points: CalibrationParam = _p(
        "max_compliance_points", 3.0, "compliance effort saturates similarly")
    human_review_points: CalibrationParam = _p(
        "human_review_points", 1.0,
        "any human-review step requires a queue, a UI and a feedback path")
    custom_capability_points: CalibrationParam = _p(
        "custom_capability_points", 0.5,
        "each capability beyond a simple pipeline adds a component to build")
    simple_pipeline_capabilities: CalibrationParam = _p(
        "simple_pipeline_capabilities", 3.0,
        "ingest + one transform + one output is treated as the baseline shape")
    realtime_points: CalibrationParam = _p(
        "realtime_points", 1.0,
        "conversational sectors add latency budgets and session handling")

    # D2: implementation kind is a MODIFIER; scope stays the primary driver,
    # so these are deliberately smaller than the integration/data terms.
    implementation_kind_points: dict[str, CalibrationParam] = Field(
        default_factory=lambda: {
            "low_code": _p("impl_low_code", 0.0,
                           "platform supplies connectors, retries and monitoring"),
            "managed_service": _p("impl_managed_service", 0.75,
                                  "vendor handles the model, integration work remains"),
            "custom_code": _p("impl_custom_code", 2.0,
                              "everything the platform would have supplied must be built"),
        })

    data_readiness_points: dict[str, CalibrationParam] = Field(
        default_factory=lambda: {
            DataReadiness.EXCELLENT.value: _p("data_excellent", 0.0, "usable as-is"),
            DataReadiness.GOOD.value: _p("data_good", 0.5, "light cleaning"),
            DataReadiness.PARTIAL.value: _p("data_partial", 1.5, "gathering and labelling"),
            DataReadiness.MINIMAL.value: _p("data_minimal", 2.5, "substantial preparation"),
            DataReadiness.NONE.value: _p("data_none", 3.5,
                                         "data must be created before anything can be built"),
        })

    scale_points: dict[str, CalibrationParam] = Field(
        default_factory=lambda: {
            "small": _p("scale_small", 0.0, "no special handling"),
            "medium": _p("scale_medium", 1.0, "batching and throughput work"),
            "large": _p("scale_large", 2.0, "queueing, backpressure and load testing"),
        })

    effort_large_threshold: CalibrationParam = _p(
        "effort_large_threshold", 6.5,
        "MVP calibration: above this the job stops fitting a single short build")
    effort_medium_threshold: CalibrationParam = _p(
        "effort_medium_threshold", 3.0,
        "MVP calibration: below this the job is a focused single-component build")
    integration_large_threshold: CalibrationParam = _p(
        "integration_large_threshold", 5.0, "MVP calibration")
    integration_medium_threshold: CalibrationParam = _p(
        "integration_medium_threshold", 2.5, "MVP calibration")

    def effort_band(self, score: float) -> EffortBand:
        if score >= self.effort_large_threshold.value:
            return EffortBand.LARGE
        if score >= self.effort_medium_threshold.value:
            return EffortBand.MEDIUM
        return EffortBand.SMALL

    def integration_band(self, score: float) -> EffortBand:
        if score >= self.integration_large_threshold.value:
            return EffortBand.LARGE
        if score >= self.integration_medium_threshold.value:
            return EffortBand.MEDIUM
        return EffortBand.SMALL

    def all_params(self) -> list[CalibrationParam]:
        out: list[CalibrationParam] = []
        for name, val in self.__dict__.items():
            if isinstance(val, CalibrationParam):
                out.append(val)
            elif isinstance(val, dict):
                out.extend(v for v in val.values() if isinstance(v, CalibrationParam))
        return out


class AlternativesCalibration(BaseModel):
    """Calibrations owned by the Alternatives module (spec 11).

    Separate from ScopeCalibration because it answers a different question:
    not "how big is this build" but "is carrying on unchanged still a live
    option the user should see".
    """

    status_quo_automation_ceiling: CalibrationParam = _p(
        "status_quo_automation_ceiling", 40.0,
        unit="percent_automation_upper_bound",
        rationale=(
            "spec 11.2 surfaces the current process only 'where it is a "
            "meaningful baseline'. Below this projected automation ceiling the "
            "AI case is weak enough that continuing unchanged is a real option "
            "the user should be shown rather than argued out of. The number is "
            "a judgement about when a comparison is worth showing, not a "
            "measurement of anything, and it gates presentation only — it "
            "never enters a score, a ranking or an economic result."),
        last_reviewed="2026-08-19")

    def all_params(self) -> list[CalibrationParam]:
        return [v for v in self.__dict__.values() if isinstance(v, CalibrationParam)]


CALIBRATION = ScopeCalibration()
ALTERNATIVES_CALIBRATION = AlternativesCalibration()

DISCLOSURE = (
    f"scope calibration v{CALIBRATION_VERSION}: every weight and threshold is an "
    f"explicit MVP assumption, not an empirical measurement — see "
    f"solution/calibration.py for each parameter's rationale"
)


def all_calibration_params() -> list[CalibrationParam]:
    """Every calibration in this module, for disclosure in a report."""
    return CALIBRATION.all_params() + ALTERNATIVES_CALIBRATION.all_params()
