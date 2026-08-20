"""Text-to-speech (speech generation) wrapper — Route B, TTS leg.

Converts interviewer text to spoken audio via the OpenAI speech API.
Server-side only.
"""

from __future__ import annotations

import os

try:
    from dotenv import load_dotenv

    load_dotenv(".env.local")
except ImportError:
    pass

from openai import OpenAI

from llm.provider import client_for

from core.costs import record_usage
from lib.logging_config import get_logger

log = get_logger("llm.tts")

MODEL = os.getenv("OPENAI_TTS_MODEL", "gpt-4o-mini-tts")
VOICE = os.getenv("OPENAI_TTS_VOICE", "alloy")


def _client() -> OpenAI:
    return client_for("TTS")



def synthesize(text: str, *, voice: str = VOICE) -> bytes:
    """Return the spoken audio for `text` as raw bytes (mp3)."""
    client = _client()
    log.info("synthesize model=%s voice=%s chars=%d", MODEL, voice, len(text))
    resp = client.audio.speech.create(model=MODEL, voice=voice, input=text)
    record_usage(purpose="audio_speech", model=MODEL,
                 usage=getattr(resp, "usage", None))
    log.info("synthesize done bytes=%d", len(resp.content))
    return resp.content
