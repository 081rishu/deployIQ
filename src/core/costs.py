"""Process-local OpenAI usage and cost telemetry.

This tracks platform API spend only. It never participates in DeployIQ's
assessment economics, scoring, report figures, or recommendation logic.
"""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from threading import Lock
from typing import Any, Optional

from core.logging import get_logger
from core.request_context import get_request_id

log = get_logger("deployiq.cost")


@dataclass(frozen=True)
class ModelPrice:
    input_per_1m_usd: float
    output_per_1m_usd: float


@dataclass(frozen=True)
class UsageEvent:
    timestamp: str
    request_id: Optional[str]
    purpose: str
    model: str
    input_tokens: Optional[int]
    output_tokens: Optional[int]
    total_tokens: Optional[int]
    estimated_usd: Optional[float]
    pricing_configured: bool


class CostTracker:
    def __init__(self) -> None:
        self._events: list[UsageEvent] = []
        self._lock = Lock()

    def record(self, event: UsageEvent) -> UsageEvent:
        with self._lock:
            self._events.append(event)
        return event

    def events(self) -> list[UsageEvent]:
        with self._lock:
            return list(self._events)

    def clear(self) -> None:
        with self._lock:
            self._events.clear()


tracker = CostTracker()


def _prices() -> dict[str, ModelPrice]:
    raw = os.getenv("DEPLOYIQ_MODEL_PRICES_JSON", "{}")
    try:
        configured = json.loads(raw)
    except json.JSONDecodeError:
        log.warning("model_price_configuration_invalid")
        return {}
    prices: dict[str, ModelPrice] = {}
    for model, value in configured.items():
        if not isinstance(value, dict):
            continue
        try:
            prices[str(model)] = ModelPrice(
                input_per_1m_usd=float(value["input_per_1m_usd"]),
                output_per_1m_usd=float(value["output_per_1m_usd"]),
            )
        except (KeyError, TypeError, ValueError):
            continue
    return prices


def extract_usage(usage: Any) -> tuple[Optional[int], Optional[int], Optional[int]]:
    if usage is None:
        return None, None, None
    if not isinstance(usage, dict):
        usage = {
            key: getattr(usage, key, None)
            for key in ("prompt_tokens", "completion_tokens", "total_tokens",
                        "input_tokens", "output_tokens")
        }
    input_tokens = usage.get("prompt_tokens", usage.get("input_tokens"))
    output_tokens = usage.get("completion_tokens", usage.get("output_tokens"))
    total_tokens = usage.get("total_tokens")
    try:
        input_tokens = int(input_tokens) if input_tokens is not None else None
        output_tokens = int(output_tokens) if output_tokens is not None else None
        total_tokens = int(total_tokens) if total_tokens is not None else None
    except (TypeError, ValueError):
        return None, None, None
    if total_tokens is None and input_tokens is not None and output_tokens is not None:
        total_tokens = input_tokens + output_tokens
    return input_tokens, output_tokens, total_tokens


def record_usage(*, purpose: str, model: str, usage: Any = None) -> UsageEvent:
    input_tokens, output_tokens, total_tokens = extract_usage(usage)
    price = _prices().get(model)
    estimated = None
    if price is not None and input_tokens is not None and output_tokens is not None:
        estimated = round(
            input_tokens * price.input_per_1m_usd / 1_000_000
            + output_tokens * price.output_per_1m_usd / 1_000_000,
            8,
        )
    event = tracker.record(UsageEvent(
        timestamp=datetime.now(timezone.utc).isoformat(),
        request_id=get_request_id(),
        purpose=purpose,
        model=model,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        total_tokens=total_tokens,
        estimated_usd=estimated,
        pricing_configured=price is not None,
    ))
    log.info(
        "llm_usage purpose=%s model=%s input_tokens=%s output_tokens=%s total_tokens=%s estimated_usd=%s pricing_configured=%s",
        purpose, model, input_tokens, output_tokens, total_tokens, estimated,
        price is not None,
    )
    return event


def event_dict(event: UsageEvent) -> dict[str, Any]:
    return asdict(event)
