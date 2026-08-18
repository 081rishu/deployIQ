"""Registry facade — deterministic candidate generation from capabilities."""

from __future__ import annotations

from solution.patterns import patterns_covering
from solution.schema import Capability, SolutionPattern

__all__ = ["patterns_covering", "Capability", "SolutionPattern"]
