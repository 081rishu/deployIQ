"""AI/API pricing registry — E2.

Pricing is applied ONLY when the selected architecture actually uses the
provider/service. A stale or unrelated provider price is never substituted:
missing pricing is ABSENT, which the engine reports rather than absorbing.

Token usage per unit is deliberately separate from token PRICE. The price is
sourced from the provider; the usage is an explicit assumption range that must
appear in sensitivity analysis.
"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Literal, Optional

from pydantic import BaseModel, Field

from core.paths import data_path
from schemas.assessment_state import Provenance, RangeEstimate

_PATH = data_path("ai_pricing.json")


class PricingRecord(BaseModel):
    pricing_id: str
    provider: str
    service: str
    unit: Literal["per_1m_tokens", "per_page", "per_month"]
    input_price: float
    output_price: float = 0.0
    currency: str
    geography: str
    effective_date: str
    source: str
    source_url: str = ""
    last_verified: str = ""
    provenance: Literal["sourced", "assumed"] = "sourced"
    notes: str = ""

    def citation(self) -> str:
        return (f"{self.provider} {self.service} — {self.source}, "
                f"effective {self.effective_date}")


class TokenUsageAssumption(BaseModel):
    input_tokens_min: float
    input_tokens_max: float
    output_tokens_min: float
    output_tokens_max: float
    provenance: str = "assumed"
    rationale: str = ""

    def input_range(self) -> RangeEstimate:
        return RangeEstimate(min=self.input_tokens_min, max=self.input_tokens_max,
                             confidence="low", provenance=Provenance.ASSUMED,
                             source=f"assumed input tokens per unit: {self.rationale}")

    def output_range(self) -> RangeEstimate:
        return RangeEstimate(min=self.output_tokens_min, max=self.output_tokens_max,
                             confidence="low", provenance=Provenance.ASSUMED,
                             source=f"assumed output tokens per unit: {self.rationale}")


class PricingBook(BaseModel):
    version: int
    description: str = ""
    records: list[PricingRecord] = Field(default_factory=list)
    token_usage_assumptions: dict = Field(default_factory=dict)

    def by_id(self, pricing_id: str) -> Optional[PricingRecord]:
        return next((r for r in self.records if r.pricing_id == pricing_id), None)

    def token_usage(self, key: str) -> Optional[TokenUsageAssumption]:
        raw = self.token_usage_assumptions.get(key)
        return TokenUsageAssumption.model_validate(raw) if raw else None


@lru_cache(maxsize=1)
def load_pricing() -> PricingBook:
    return PricingBook.model_validate(json.loads(_PATH.read_text(encoding="utf-8")))


def record(pricing_id: str) -> Optional[PricingRecord]:
    return load_pricing().by_id(pricing_id)
