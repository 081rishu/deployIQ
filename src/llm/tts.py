"""Text-to-speech (speech generation) wrapper — Route B, TTS leg.

Converts interviewer text to spoken audio via the OpenAI speech API.
Server-side only.
"""

from __future__ import annotations

import os
from typing import Optional

try:
    from dotenv import load_dotenv

    load_dotenv(".env.local")
except ImportError:
    pass

from openai import OpenAI

from llm.provider import execute

from core.costs import record_usage
from lib.logging_config import get_logger

log = get_logger("llm.tts")

MODEL = os.getenv("OPENAI_TTS_MODEL", "gpt-4o-mini-tts")
VOICE = os.getenv("OPENAI_TTS_VOICE", "alloy")
# Only used when the endpoint does not declare its own container.
FORMAT = os.getenv("OPENAI_TTS_FORMAT", "mp3")





def synthesize(text: str, *, voice: Optional[str] = None) -> tuple[bytes, str]:
    """Return the spoken audio for `text`, with the container it came back in.

    The format is returned rather than assumed because it is not the caller's
    choice: Groq's Orpheus emits WAV only and rejects the SDK's mp3 default,
    OpenAI speaks mp3. The browser needs the right MIME type to play it, so
    the container travels with the bytes instead of being hardcoded at both
    ends.
    """
    def call(client: OpenAI, endpoint) -> tuple[bytes, str]:
        selected = endpoint.model or MODEL
        fmt = endpoint.audio_format or FORMAT
        chosen_voice = voice or endpoint.voice or VOICE
        log.info("synthesize model=%s voice=%s fmt=%s chars=%d",
                 selected, chosen_voice, fmt, len(text))
        resp = client.audio.speech.create(model=selected, voice=chosen_voice,
                                          response_format=fmt, input=text)
        record_usage(purpose="audio_speech", model=selected,
                     usage=getattr(resp, "usage", None))
        log.info("synthesize done bytes=%d", len(resp.content))
        return resp.content, fmt

    return execute("TTS", call)
