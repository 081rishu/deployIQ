"""Task/architecture-specific performance metrics (P0.1).

Metrics are tied to the SELECTED architecture, not forced sector-wide. A
deterministic document workflow does not get LLM hallucination metrics just
because the sector is document processing. Which metrics are relevant is
determined by the chosen pattern.
"""

from __future__ import annotations

from typing import Optional

from lib.benchmarks import figure as benchmark_figure
from schemas.assessment_state import Provenance, RangeEstimate, Sector
from solution.schema import PerformanceMetric

# metric name -> benchmark pack key that measures the same thing. Where a
# sourced figure exists it replaces the placeholder assumption, so the metric
# enters the analysis tagged `sourced` with a citation (spec 6).
_BENCHMARK_KEYS = {
    "stp_rate": "straight_through_processing_rate",
    "exception_rate": "invoice_exception_rate",
}

# pattern_id -> metric names relevant to that architecture
_PATTERN_METRICS = {
    "ai_assisted_workflow": ["resolution_rate", "escalation_rate", "tool_execution_reliability"],
    "rag_knowledge_assistant": ["answer_accuracy", "retrieval_precision", "hallucination_rate"],
    "voice_agent": ["resolution_rate", "escalation_rate", "tool_execution_reliability"],
    "document_pipeline": ["extraction_accuracy", "stp_rate", "exception_rate"],
    # A deterministic ruleset is not measured on model accuracy — it is either
    # right or it never fired. What matters is how much of the real caseload a
    # rule actually matches, and what happens to the rest.
    "rules_based_workflow": ["rule_coverage", "fallthrough_rate",
                             "tool_execution_reliability"],
}

# Plausible assumption ranges per metric, marked assumption provenance.
# Replace with sourced benchmark data as it becomes available.
_ASSUMED_RANGES = {
    "resolution_rate": (70, 90),
    "escalation_rate": (10, 30),
    "tool_execution_reliability": (85, 98),
    "answer_accuracy": (80, 95),
    "retrieval_precision": (75, 90),
    "hallucination_rate": (1, 8),
    "extraction_accuracy": (85, 98),
    "stp_rate": (60, 85),
    "exception_rate": (5, 20),
    "error_rate": (2, 10),
    "rule_coverage": (50, 80),
    "fallthrough_rate": (20, 50),
}


def metrics_for(pattern_id: str, sector: Optional[Sector] = None) -> list[PerformanceMetric]:
    """Return the performance metrics relevant to the selected architecture.

    A metric backed by a sourced figure in the sector's benchmark pack uses
    that figure and carries its citation; the rest stay explicit assumptions
    until sourced.
    """
    names = _PATTERN_METRICS.get(pattern_id, ["error_rate"])
    out: list[PerformanceMetric] = []
    for name in names:
        estimate = None
        if sector is not None and name in _BENCHMARK_KEYS:
            fig = benchmark_figure(sector, _BENCHMARK_KEYS[name])
            if fig is not None and fig.provenance == "sourced":
                estimate = fig.as_range()
        if estimate is None:
            lo, hi = _ASSUMED_RANGES.get(name, (0, 100))
            estimate = RangeEstimate(
                min=lo, max=hi, confidence="low",
                provenance=Provenance.ASSUMED,
                source="placeholder assumption — replace with sourced benchmark per metric",
            )
        out.append(PerformanceMetric(metric=name, estimate=estimate))
    return out
