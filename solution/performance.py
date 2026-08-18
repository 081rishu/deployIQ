"""Task/architecture-specific performance metrics (P0.1).

Metrics are tied to the SELECTED architecture, not forced sector-wide. A
deterministic document workflow does not get LLM hallucination metrics just
because the sector is document processing. Which metrics are relevant is
determined by the chosen pattern.
"""

from __future__ import annotations

from solution.schema import PerformanceMetric, Provenance, RangeEstimate

# pattern_id -> metric names relevant to that architecture
_PATTERN_METRICS = {
    "ai_assisted_workflow": ["resolution_rate", "escalation_rate", "tool_execution_reliability"],
    "rag_knowledge_assistant": ["answer_accuracy", "retrieval_precision", "hallucination_rate"],
    "voice_agent": ["resolution_rate", "escalation_rate", "tool_execution_reliability"],
    "document_pipeline": ["extraction_accuracy", "stp_rate", "exception_rate"],
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
}


def metrics_for(pattern_id: str) -> list[PerformanceMetric]:
    """Return the performance metrics relevant to the selected architecture.

    Values carry assumption provenance with an explicit note until benchmark
    data is sourced.
    """
    names = _PATTERN_METRICS.get(pattern_id, ["error_rate"])
    out: list[PerformanceMetric] = []
    for name in names:
        lo, hi = _ASSUMED_RANGES.get(name, (0, 100))
        out.append(PerformanceMetric(
            metric=name,
            estimate=RangeEstimate(
                min=lo, max=hi, confidence="low",
                provenance=Provenance.ASSUMPTION,
                source="placeholder assumption — replace with sourced benchmark per metric",
            ),
        ))
    return out
