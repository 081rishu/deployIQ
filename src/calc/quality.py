"""Output quality, defined symmetrically — E6.

The engine previously discounted the AI side for errors while assuming the
current process produced 100% valid output. That flatters the human baseline
and is the wrong direction for a tool meant to be able to steer away from AI.

Two rules:

1. NO 100% DEFAULT. With no evidence about current quality, current valid
   output is ABSENT, not perfect.

2. METRICS ARE NOT INTERCHANGEABLE. A 14% exception rate supports an "86%
   non-exception rate". It does NOT populate a field called `accuracy`. A
   comparison is only made when both sides measure the same thing.
"""

from __future__ import annotations

from enum import Enum
from typing import Optional

from pydantic import BaseModel

from schemas.assessment_state import Provenance, RangeEstimate


class QualityMetric(str, Enum):
    """What is actually being measured. Never silently converted."""
    NON_EXCEPTION_RATE = "non_exception_rate"
    EXTRACTION_ACCURACY = "extraction_accuracy"
    ANSWER_ACCURACY = "answer_accuracy"
    RESOLUTION_RATE = "resolution_rate"
    STRAIGHT_THROUGH_RATE = "straight_through_rate"


# Which estimator metric names map to which semantic metric.
_METRIC_NAMES = {
    "extraction_accuracy": QualityMetric.EXTRACTION_ACCURACY,
    "answer_accuracy": QualityMetric.ANSWER_ACCURACY,
    "resolution_rate": QualityMetric.RESOLUTION_RATE,
    "stp_rate": QualityMetric.STRAIGHT_THROUGH_RATE,
}
# An exception rate is the complement of a NON-EXCEPTION rate — nothing else.
_COMPLEMENT_NAMES = {"exception_rate": QualityMetric.NON_EXCEPTION_RATE}


class QualityObservation(BaseModel):
    metric: QualityMetric
    value: Optional[RangeEstimate] = None      # fraction 0-1
    available: bool = True
    basis: str = ""


class QualityComparison(BaseModel):
    """A comparison only exists when both sides measure the same thing."""
    comparable: bool
    metric: Optional[QualityMetric] = None
    current: Optional[QualityObservation] = None
    expected: Optional[QualityObservation] = None
    statement: str = ""


# What the interviewer collects -> what it MEANS semantically. Only exact
# semantic matches are mapped; nothing is renamed into something else.
_COLLECTED_TO_SEMANTIC = {
    "first_contact_resolution": QualityMetric.RESOLUTION_RATE,
    "straight_through_rate": QualityMetric.STRAIGHT_THROUGH_RATE,
    # An exception rate is the complement of a NON-EXCEPTION rate, never an
    # accuracy. First-pass yield is the share passing without rework, which is
    # the same quantity as non-exception.
    "exception_rate": QualityMetric.NON_EXCEPTION_RATE,
    "first_pass_yield": QualityMetric.NON_EXCEPTION_RATE,
}
# Metrics collected as a FAILURE share, so the stored value is complemented.
_COLLECTED_IS_COMPLEMENT = {"exception_rate"}
# Collected metrics with no comparable AI-side counterpart yet.
_COLLECTED_UNMAPPED = {"escalation_rate", "rework_rate"}


def from_collected(metric_name: str, value: RangeEstimate) -> Optional[QualityObservation]:
    """Read the interviewer's current-quality answer into a semantic metric.

    Returns None when the collected metric has no comparable counterpart on the
    AI side — reporting no comparison is correct, inventing one is not.
    """
    semantic = _COLLECTED_TO_SEMANTIC.get(metric_name)
    if semantic is None:
        return None
    if metric_name in _COLLECTED_IS_COMPLEMENT:
        val = RangeEstimate(min=1.0 - value.max, max=1.0 - value.min,
                            confidence=value.confidence,
                            provenance=Provenance.DERIVED,
                            source=f"1 - {metric_name} (user-reported)")
    else:
        val = value
    return QualityObservation(metric=semantic, value=val,
                              basis=f"user-reported {metric_name}")


def absent(metric: QualityMetric, why: str) -> QualityObservation:
    return QualityObservation(metric=metric, value=None, available=False, basis=why)


def from_estimator_metric(name: str, estimate: RangeEstimate) -> Optional[QualityObservation]:
    """Read a performance metric into a semantically-named observation."""
    if name in _METRIC_NAMES:
        return QualityObservation(
            metric=_METRIC_NAMES[name],
            value=RangeEstimate(min=estimate.min / 100.0, max=estimate.max / 100.0,
                                confidence=estimate.confidence,
                                provenance=estimate.provenance, source=estimate.source,
                                source_id=estimate.source_id),
            basis=f"estimator metric '{name}'")
    if name in _COMPLEMENT_NAMES:
        # 14% exceptions -> 86% NON-EXCEPTION. Deliberately not called accuracy.
        return QualityObservation(
            metric=_COMPLEMENT_NAMES[name],
            value=RangeEstimate(min=(100.0 - estimate.max) / 100.0,
                                max=(100.0 - estimate.min) / 100.0,
                                confidence=estimate.confidence,
                                provenance=Provenance.DERIVED,
                                source=f"1 - {name}: {estimate.source}",
                                source_id=estimate.source_id),
            basis=f"complement of estimator metric '{name}'")
    return None


def compare(
    current: Optional[QualityObservation], expected: Optional[QualityObservation],
) -> QualityComparison:
    """Compare current and AI quality only when the metric matches (E6)."""
    if current is None or not current.available:
        why = (current.basis if current is not None and current.basis
               else "current-process quality was never measured")
        return QualityComparison(
            comparable=False, expected=expected, current=current,
            statement=(f"{why}, so current quality is ABSENT. It is NOT assumed to "
                       f"be 100% — doing so would discount the AI side for errors "
                       f"while giving the human side a free pass."))
    if expected is None or not expected.available:
        return QualityComparison(
            comparable=False, current=current, expected=expected,
            statement="no expected AI quality metric is available for comparison")
    if current.metric != expected.metric:
        return QualityComparison(
            comparable=False, current=current, expected=expected,
            statement=(f"not comparable: the current process is measured as "
                       f"'{current.metric.value}' and the AI process as "
                       f"'{expected.metric.value}'. These are different quantities "
                       f"and comparing them would invent a result."))
    return QualityComparison(
        comparable=True, metric=current.metric, current=current, expected=expected,
        statement=(f"both sides measured as '{current.metric.value}': current "
                   f"{current.value.min:.1%}-{current.value.max:.1%} vs expected "
                   f"{expected.value.min:.1%}-{expected.value.max:.1%}"))
