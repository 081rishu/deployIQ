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
) -> str:
    """Transcribe audio to text.

    `audio` may be a path, raw bytes, or a file-like object. Raw bytes need a
    filename so OpenAI can infer the container format.
    """
    client = _client()
    log.info("transcribe model=%s lang=%s filename=%s", MODEL, language, filename)
    resp = client.audio.transcriptions.create(
        model=MODEL,
        file=(filename, audio),
        language=language,
    )
    text = resp
    if hasattr(resp, "text"):
        text = resp.text
    log.info("transcribe done chars=%d", len(str(text)))
    return str(text).strip()
