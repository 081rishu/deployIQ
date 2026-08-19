"""Canonical shared constants — N5.

Scale thresholds were duplicated in ranking.py and scope.py. One definition,
imported by both, so changing a threshold changes every consumer (acceptance
test L).
"""

from __future__ import annotations

# Monthly-volume boundaries for the scale bands used against
# Compatibility.scale and the scope model.
SCALE_MEDIUM_FROM = 10_000
SCALE_LARGE_FROM = 50_000


def scale_band(monthly_volume: float | None) -> str:
    vol = monthly_volume or 0
    if vol >= SCALE_LARGE_FROM:
        return "large"
    if vol >= SCALE_MEDIUM_FROM:
        return "medium"
    return "small"
