"""Which OpenAI-compatible endpoint each leg of the pipeline talks to.

The three legs — chat, transcription, speech — are configured separately on
purpose. Free and low-cost providers rarely cover all three: Groq serves chat
and Whisper transcription but its speech model is gated, Gemini serves chat
but exposes no /audio/transcriptions at all. Splitting them means a working
setup can be assembled from whatever each provider actually offers, instead
of the whole interview being held hostage to the weakest leg.

Resolution order for each leg, first hit wins:

    DEPLOYIQ_<LEG>_BASE_URL   ->  OPENAI_BASE_URL   ->  provider default
    DEPLOYIQ_<LEG>_API_KEY    ->  OPENAI_API_KEY

An unset base URL means the OpenAI default, so an existing .env with nothing
but OPENAI_API_KEY keeps working exactly as before.
"""

from __future__ import annotations

import os
from typing import Literal, Optional

from openai import OpenAI

Leg = Literal["LLM", "STT", "TTS"]


def base_url_for(leg: Leg) -> Optional[str]:
    return (os.getenv(f"DEPLOYIQ_{leg}_BASE_URL")
            or os.getenv("OPENAI_BASE_URL")
            or None)


def api_key_for(leg: Leg) -> str:
    key = (os.getenv(f"DEPLOYIQ_{leg}_API_KEY")
           or os.getenv("OPENAI_API_KEY"))
    if not key:
        raise RuntimeError(
            f"No API key for the {leg} leg — set DEPLOYIQ_{leg}_API_KEY or "
            f"OPENAI_API_KEY in .env"
        )
    return key


def client_for(leg: Leg) -> OpenAI:
    """An SDK client pointed at whichever provider serves this leg."""
    base = base_url_for(leg)
    return OpenAI(api_key=api_key_for(leg), **({"base_url": base} if base else {}))


def describe() -> dict[str, str]:
    """Where each leg is pointed, for a startup log. Never includes a key."""
    return {leg: (base_url_for(leg) or "api.openai.com (default)")
            for leg in ("LLM", "STT", "TTS")}
