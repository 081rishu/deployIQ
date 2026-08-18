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

from lib.logging_config import get_logger

log = get_logger("llm.tts")

MODEL = os.getenv("OPENAI_TTS_MODEL", "gpt-4o-mini-tts")
VOICE = os.getenv("OPENAI_TTS_VOICE", "alloy")


def _client() -> OpenAI:
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        raise RuntimeError("OPENAI_API_KEY is not set — add it to .env.local")
    return OpenAI(api_key=api_key)


def synthesize(text: str, *, voice: str = VOICE) -> bytes:
    """Return the spoken audio for `text` as raw bytes (mp3)."""
    client = _client()
    log.info("synthesize model=%s voice=%s chars=%d", MODEL, voice, len(text))
    resp = client.audio.speech.create(model=MODEL, voice=voice, input=text)
    log.info("synthesize done bytes=%d", len(resp.content))
    return resp.content
