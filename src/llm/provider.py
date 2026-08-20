"""Which endpoint each leg of the pipeline talks to, and what to do when one
runs out.

Two problems are solved here.

FIRST, the three legs — chat, transcription, speech — are configured
separately, because free providers rarely cover all three. Groq serves chat
and Whisper transcription but gates its speech model; Gemini serves chat and
exposes no /audio/transcriptions at all.

SECOND, a leg may have SEVERAL endpoints. Free tiers are rate limited per
key, so the practical way to get usable throughput is to spread work across
keys and fail over when one is exhausted. An endpoint carries its own model,
because the pool spans providers: llama-3.3-70b on Groq and gemini-2.0-flash
on Google are the same leg but not the same model name.

Configuration, in precedence order:

  DEPLOYIQ_PROVIDER_POOL   path to a JSON file, or inline JSON
  DEPLOYIQ_<LEG>_API_KEYS  comma-separated keys for one base URL
  DEPLOYIQ_<LEG>_API_KEY   a single key            (unchanged behaviour)
  OPENAI_API_KEY           the original single key (unchanged behaviour)

Pool file shape:

  {
    "LLM": [
      {"base_url": "https://api.groq.com/openai/v1",
       "model": "llama-3.3-70b-versatile",
       "keys": ["gsk_one", "gsk_two"]},
      {"base_url": "https://generativelanguage.googleapis.com/v1beta/openai/",
       "model": "gemini-2.0-flash",
       "keys": ["AIza_one"]}
    ],
    "STT": [ ... ]
  }

Keys are never logged. Endpoints are identified by host and key fingerprint.
"""

from __future__ import annotations

import hashlib
import itertools
import json
import os
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Literal, Optional
from urllib.parse import urlparse

from openai import OpenAI

from lib.logging_config import get_logger

log = get_logger("llm.provider")

Leg = Literal["LLM", "STT", "TTS"]
LEGS: tuple[Leg, ...] = ("LLM", "STT", "TTS")

# How long an endpoint sits out after reporting exhaustion. Long enough that a
# per-minute free-tier window resets, short enough that a key is not lost for
# the rest of a session.
COOLDOWN_SECONDS = 65.0


class ProvidersExhausted(RuntimeError):
    """Every endpoint for a leg is rate limited or out of quota."""


@dataclass
class Endpoint:
    base_url: Optional[str]
    api_key: str
    model: Optional[str] = None
    cooldown_until: float = 0.0

    @property
    def label(self) -> str:
        """Identifies the endpoint in logs without exposing the key."""
        host = urlparse(self.base_url).netloc if self.base_url else "api.openai.com"
        return f"{host}#{hashlib.sha256(self.api_key.encode()).hexdigest()[:6]}"

    @property
    def available(self) -> bool:
        return time.monotonic() >= self.cooldown_until

    def cool_down(self) -> None:
        self.cooldown_until = time.monotonic() + COOLDOWN_SECONDS


@dataclass
class Pool:
    leg: Leg
    endpoints: list[Endpoint] = field(default_factory=list)
    _turn: Any = None

    def __post_init__(self) -> None:
        self._turn = itertools.cycle(range(max(1, len(self.endpoints))))

    def ordered(self) -> list[Endpoint]:
        """Endpoints to try, rotated so work spreads instead of hammering the
        first key until it dies."""
        if not self.endpoints:
            return []
        start = next(self._turn) % len(self.endpoints)
        rotated = self.endpoints[start:] + self.endpoints[:start]
        return [e for e in rotated if e.available]


_pools: dict[Leg, Pool] = {}
_lock = threading.Lock()


def _split(raw: Optional[str]) -> list[str]:
    return [k.strip() for k in (raw or "").split(",") if k.strip()]


def _pool_config() -> dict:
    raw = os.getenv("DEPLOYIQ_PROVIDER_POOL", "").strip()
    if not raw:
        return {}
    if not raw.startswith("{"):
        if not os.path.exists(raw):
            log.warning("DEPLOYIQ_PROVIDER_POOL points at %s, which does not exist", raw)
            return {}
        raw = open(raw, encoding="utf-8").read()
    try:
        return json.loads(raw)
    except json.JSONDecodeError as exc:
        # Explicit rather than silent: a malformed pool would otherwise fall
        # back to a single key and look like a quota problem later.
        raise RuntimeError(f"DEPLOYIQ_PROVIDER_POOL is not valid JSON: {exc}") from exc


def _build(leg: Leg) -> Pool:
    endpoints: list[Endpoint] = []
    for entry in _pool_config().get(leg, []) or []:
        keys = entry.get("keys") or ([entry["api_key"]] if entry.get("api_key") else [])
        for key in keys:
            endpoints.append(Endpoint(base_url=entry.get("base_url") or None,
                                      api_key=key, model=entry.get("model") or None))
    if not endpoints:
        base = (os.getenv(f"DEPLOYIQ_{leg}_BASE_URL")
                or os.getenv("OPENAI_BASE_URL") or None)
        keys = (_split(os.getenv(f"DEPLOYIQ_{leg}_API_KEYS"))
                or _split(os.getenv(f"DEPLOYIQ_{leg}_API_KEY"))
                or _split(os.getenv("OPENAI_API_KEY")))
        model = os.getenv(f"DEPLOYIQ_{leg}_MODEL") or None
        endpoints = [Endpoint(base_url=base, api_key=k, model=model) for k in keys]
    return Pool(leg=leg, endpoints=endpoints)


def pool(leg: Leg) -> Pool:
    with _lock:
        if leg not in _pools:
            _pools[leg] = _build(leg)
        return _pools[leg]


def reset() -> None:
    """Drop cached pools so a changed environment takes effect (tests)."""
    with _lock:
        _pools.clear()


def _is_exhausted(exc: Exception) -> bool:
    """Is this 'this key is spent', as opposed to a real failure?

    Matched on the exception type and HTTP status rather than message text, so
    a wording change upstream cannot turn a rate limit into a hard failure
    that skips the remaining keys.
    """
    if type(exc).__name__ in ("RateLimitError", "AuthenticationError",
                              "PermissionDeniedError"):
        return True
    return getattr(exc, "status_code", None) in (401, 403, 429)


def execute(leg: Leg, call: Callable[[OpenAI, Optional[str]], Any],
            *, model: Optional[str] = None) -> Any:
    """Run `call` against the first endpoint that answers.

    `call` receives an SDK client and the model for that endpoint — the model
    travels with the endpoint because the pool can span providers.

    An exhausted endpoint is cooled down and the next is tried. Anything else
    propagates immediately: a malformed request is not fixed by another key,
    and retrying it across the pool would burn every one of them.
    """
    p = pool(leg)
    candidates = p.ordered()
    if not candidates:
        raise ProvidersExhausted(
            f"No endpoint available for the {leg} leg "
            f"({len(p.endpoints)} configured, all cooling down or none set)")

    spent: list[str] = []
    for endpoint in candidates:
        client = OpenAI(api_key=endpoint.api_key,
                        **({"base_url": endpoint.base_url} if endpoint.base_url else {}))
        try:
            return call(client, model or endpoint.model)
        except Exception as exc:  # noqa: BLE001 - classified immediately below
            if not _is_exhausted(exc):
                raise
            endpoint.cool_down()
            spent.append(endpoint.label)
            log.warning("endpoint %s exhausted for %s; failing over", endpoint.label, leg)
    raise ProvidersExhausted(
        f"Every {leg} endpoint is exhausted (tried {', '.join(spent)}). "
        f"Add another key or wait for the rate-limit window to reset.")


def describe() -> dict[str, str]:
    """Where each leg points, for a startup log. Never includes a key."""
    return {leg: (", ".join(e.label for e in pool(leg).endpoints) or "not configured")
            for leg in LEGS}
