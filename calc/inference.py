"""Architecture-driven AI inference cost — E2 / section 17.

The engine used to hold one per-sector price key: document processing got a
Textract page price and customer support got nothing at all, which made
support AI look free. Cost now follows the SELECTED implementation and its
providers.

Two rules:
  * a provider's price is used only when the selected architecture uses that
    provider;
  * token usage per unit is an explicit assumption RANGE, kept separate from
    the sourced token PRICE, and exposed to sensitivity.
"""

from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, Field

from calc.models import CostLine, add, money, mul, scale
from lib.pricing import PricingRecord, load_pricing
from schemas.assessment_state import Provenance, RangeEstimate, Sector
from solution.schema import SolutionEstimate

# Which registry provider maps to which pricing record.
_PROVIDER_PRICING = {
    "llm_api": "openai_gpt5_mini_v1",
    "rag_retrieval": "openai_gpt5_mini_v1",
    "openai_realtime": "openai_gpt5_v1",
}
# Document pipelines that extract structure use the expense API.
_DOC_EXTRACTION_PRICING = "aws_textract_expense_v1"

TOKENS_PER_MILLION = 1_000_000


class InferenceCost(BaseModel):
    line: CostLine
    pricing_ids: list[str] = Field(default_factory=list)
    usage_assumption: Optional[str] = None
    lineage: list[str] = Field(default_factory=list)
    currency: Optional[str] = None
    currency_mismatch: Optional[str] = None


def _token_cost(
    rec: PricingRecord, annual_units: RangeEstimate, usage_key: str,
) -> tuple[Optional[RangeEstimate], list[str]]:
    usage = load_pricing().token_usage(usage_key)
    if usage is None:
        return None, []
    in_tokens = mul(annual_units, usage.input_range(), source="units x input tokens")
    out_tokens = mul(annual_units, usage.output_range(), source="units x output tokens")
    in_cost = scale(in_tokens, rec.input_price / TOKENS_PER_MILLION,
                    source=f"input tokens x {rec.input_price}/1M [{rec.pricing_id}]")
    out_cost = scale(out_tokens, rec.output_price / TOKENS_PER_MILLION,
                     source=f"output tokens x {rec.output_price}/1M [{rec.pricing_id}]")
    total = add(in_cost, out_cost, source=f"{rec.citation()} applied to assumed usage")
    return total, ["annual volume", "tokens per unit (assumed)",
                   f"token price ({rec.pricing_id}, sourced)"]


def inference_cost(
    sector: Sector, solution: SolutionEstimate,
    annual_volume: Optional[RangeEstimate],
    implementation_id: str = "", provider_ids: Optional[list[str]] = None,
    baseline_currency: Optional[str] = None,
) -> InferenceCost:
    """Inference cost for the architecture actually selected.

    Currency consistency is mandatory: provider list prices are in USD, so an
    INR-denominated assessment cannot simply add them to its labor lines. No
    implicit FX conversion is performed — the line is reported ABSENT with the
    mismatch stated, because a silently mixed-currency total is worse than a
    missing one.
    """
    if annual_volume is None:
        return InferenceCost(line=CostLine.absent(
            "inference", "AI / API inference",
            "no annual volume, so per-unit pricing cannot be applied"))

    providers = provider_ids or []
    parts, ids, lineage = [], [], []

    # Document extraction: a per-page managed service, where the pattern uses one.
    if sector == Sector.DOCUMENT_PROCESSING and solution.recommended_pattern == "document_pipeline":
        rec = load_pricing().by_id(_DOC_EXTRACTION_PRICING)
        if rec is not None:
            automated = scale(annual_volume,
                              (solution.overall_automation.min +
                               solution.overall_automation.max) / 200.0,
                              source="annual volume x automated share")
            parts.append(scale(automated, rec.input_price,
                               source=f"pages x {rec.input_price}/page [{rec.pricing_id}]"))
            ids.append(rec.pricing_id)
            lineage.extend(["annual volume", "automated share",
                            f"page price ({rec.pricing_id}, sourced)"])

    # Token-based cost wherever the selected stack includes an LLM provider.
    llm_providers = [p for p in providers if p in _PROVIDER_PRICING]
    if llm_providers:
        rec = load_pricing().by_id(_PROVIDER_PRICING[llm_providers[0]])
        if rec is not None:
            usage_key = ("customer_support_ticket" if sector == Sector.CUSTOMER_SUPPORT
                         else "customer_support_ticket")
            cost, lin = _token_cost(rec, annual_volume, usage_key)
            if cost is not None:
                parts.append(cost)
                ids.append(rec.pricing_id)
                lineage.extend(lin)

    if not parts:
        return InferenceCost(line=CostLine.absent(
            "inference", "AI / API inference",
            f"no pricing record matches the selected implementation "
            f"{implementation_id!r} and its providers {providers}. Reported as "
            f"absent rather than priced from an unrelated provider."))

    price_currency = "USD"
    if baseline_currency and baseline_currency.upper() != price_currency:
        return InferenceCost(
            line=CostLine.absent(
                "inference", "AI / API inference",
                f"provider list pricing is in {price_currency} but this assessment's "
                f"labor baseline is in {baseline_currency}. No implicit conversion is "
                f"applied, so the inference cost is reported as absent rather than "
                f"added across currencies. An explicit FX rate would be required."),
            pricing_ids=ids, currency=price_currency,
            currency_mismatch=(f"{price_currency} pricing vs {baseline_currency} "
                               f"baseline"))

    usage = load_pricing().token_usage("customer_support_ticket")
    return InferenceCost(
        currency=price_currency,
        line=CostLine(
            key="inference", label="AI / API inference",
            amount=add(*parts, source="sum of per-service inference cost"),
            note=(f"priced from {', '.join(ids)}; token usage is an explicit "
                  f"assumption and appears in sensitivity")),
        pricing_ids=ids,
        usage_assumption=(usage.rationale if usage and llm_providers else None),
        lineage=lineage)
