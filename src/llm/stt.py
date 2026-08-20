"""Speech-to-text (transcription) wrapper — Route B, STT leg.

Transcribes an audio file/buffer to text via the OpenAI transcriptions API.
Server-side only.
"""

from __future__ import annotations

import os
from typing import BinaryIO, Union

try:
    from dotenv import load_dotenv

    load_dotenv(".env.local")
except ImportError:
    pass

from openai import OpenAI

from core.costs import record_usage
from lib.logging_config import get_logger

log = get_logger("llm.stt")

MODEL = os.getenv("OPENAI_STT_MODEL", "gpt-4o-transcribe")


def _client() -> OpenAI:
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        raise RuntimeError("OPENAI_API_KEY is not set — add it to .env.local")
    return OpenAI(api_key=api_key)


def transcribe_audio(
    audio: Union[str, bytes, BinaryIO],
    *,
    language: str = "en",
    filename: str = "audio.webm",
    prompt: str = "",
) -> str:
    """Transcribe audio to text.

    `audio` may be a path, raw bytes, or a file-like object. Raw bytes need a
    filename so OpenAI can infer the container format.

    `prompt` biases DECODING toward the vocabulary of this domain — terms like
    "first-contact resolution" or "straight-through processing" that a general
    model renders as something else entirely. It is a spelling aid, nothing
    more: it must never contain an expected answer, a number, or anything the
    user has not said, because a transcription prompt does bias output and
    putting words in the user's mouth would fabricate assessment input.
    """
    client = _client()
    log.info("transcribe model=%s lang=%s filename=%s prompt_chars=%d",
             MODEL, language, filename, len(prompt))
    resp = client.audio.transcriptions.create(
        model=MODEL,
        file=(filename, audio),
        language=language,
        **({"prompt": prompt} if prompt else {}),
    )
    text = resp
    if hasattr(resp, "text"):
        text = resp.text
    record_usage(purpose="audio_transcription", model=MODEL,
                 usage=getattr(resp, "usage", None))
    log.info("transcribe done chars=%d", len(str(text)))
    return str(text).strip()
