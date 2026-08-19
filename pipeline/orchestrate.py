from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Optional

from calc import driver_ranking, sensitivity as sensitivity_mod
from calc.ai_state import LaborRealization
from calc.assessment_confidence import AssessmentConfidence
from calc.engine import EconomicInputError
from report import assemble, narrate as narrate_mod, render, validate
from report.schema import LaborRealizationSource, Report, ReportInput, ValidationResult
from schemas.assessment_state import AssessmentState
from solution import alternatives as alternatives_mod
from solution import estimator
from solution.schema import AlternativesResult, SolutionEstimate


@dataclass(frozen=True)
class AssessmentRun:
    state: AssessmentState
    solution: SolutionEstimate
    alternatives: AlternativesResult
    drivers: Optional[driver_ranking.DecisionDrivers]
    sensitivity: Optional[sensitivity_mod.SensitivityReport]
    confidence: Optional[AssessmentConfidence]
    bundle: ReportInput
    deterministic_report: Report
    deterministic_validation: ValidationResult
    final_report: Report
    final_validation: ValidationResult
    rendered: render.RenderedReport
    used_narration: bool
    narration_issues: list[str]


def _stamp_generated_at(report_obj: Report, generated_at: str) -> Report:
    if report_obj.manifest.generated_at == generated_at:
        return report_obj
    return report_obj.model_copy(update={
        "manifest": report_obj.manifest.model_copy(update={"generated_at": generated_at})
    })


def _refusal_reasons(solution: SolutionEstimate) -> list[str]:
    if solution.compliance_gap:
        return [solution.compliance_statement or
                "a hard compliance requirement could not be satisfied"]
    if solution.needs_more_information:
        return list(solution.needs_more_information)
    if solution.confidence_notes:
        return list(solution.confidence_notes)
    return ["estimator refused: no architecture was selected"]


def run_assessment(
    state: AssessmentState,
    *,
    labor_realization: Optional[LaborRealization],
    labor_realization_source: LaborRealizationSource = LaborRealizationSource.UNSET,
    enable_narration: bool = False,
    narration_complete_json: Optional[Callable[..., dict]] = None,
    narration_temperature: float = 0.2,
    narration_model: Optional[str] = None,
    generated_at: str = "",
) -> AssessmentRun:
    """Run one canonical assessment pipeline from state to rendered report."""
    solution = estimator.estimate(state)
    alternatives = alternatives_mod.derive(state, solution)

    drivers: Optional[driver_ranking.DecisionDrivers] = None
    sensitivity: Optional[sensitivity_mod.SensitivityReport] = None
    confidence: Optional[AssessmentConfidence] = None
    economic_error: list[str] = []

    if solution.recommended_pattern and not solution.compliance_gap:
        if labor_realization is None:
            economic_error = [
                "labor realization policy is required before economics can run"
            ]
        else:
            try:
                drivers = driver_ranking.rank_drivers(
                    state, solution, labor_realization
                )
                sensitivity = sensitivity_mod.sweep(
                    state, solution, labor_realization
                )
            except EconomicInputError as exc:
                economic_error = list(exc.reasons)
    else:
        economic_error = _refusal_reasons(solution)

    if drivers is not None and drivers.scores.confidence is not None:
        confidence = AssessmentConfidence.model_validate(drivers.scores.confidence)

    source = labor_realization_source if labor_realization is not None \
        else LaborRealizationSource.UNSET
    bundle = ReportInput.from_pipeline(
        state=state,
        solution=solution,
        drivers=drivers,
        alternatives=alternatives,
        sensitivity=sensitivity,
        confidence=confidence,
        labor_realization=labor_realization,
        labor_realization_source=source,
        economic_error=economic_error,
    )

    deterministic_report = _stamp_generated_at(assemble.assemble(bundle), generated_at)
    deterministic_validation = validate.validate(deterministic_report, bundle)
    if not deterministic_validation.valid:
        raise ValueError(
            "deterministic report failed validation: "
            f"{[e.code for e in deterministic_validation.errors]}"
        )

    final_report = deterministic_report
    used_narration = False
    narration_issues: list[str] = []

    if enable_narration:
        narration = narrate_mod.narrate(
            deterministic_report,
            bundle,
            complete_json=narration_complete_json,
            temperature=narration_temperature,
            model=narration_model,
        )
        narration_issues = list(narration.issues)
        candidate = _stamp_generated_at(narration.report, generated_at)
        candidate_validation = validate.validate(candidate, bundle)
        if narration.used_narration and candidate_validation.valid:
            final_report = candidate
            used_narration = True

    final_validation = validate.validate(final_report, bundle)
    if not final_validation.valid:
        raise ValueError(
            "final report failed validation: "
            f"{[e.code for e in final_validation.errors]}"
        )

    rendered = render.render(final_report, bundle)

    return AssessmentRun(
        state=state,
        solution=solution,
        alternatives=alternatives,
        drivers=drivers,
        sensitivity=sensitivity,
        confidence=confidence,
        bundle=bundle,
        deterministic_report=deterministic_report,
        deterministic_validation=deterministic_validation,
        final_report=final_report,
        final_validation=final_validation,
        rendered=rendered,
        used_narration=used_narration,
        narration_issues=narration_issues,
    )
