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

from llm.provider import execute

from core.costs import record_usage
from lib.logging_config import get_logger

log = get_logger("llm.stt")

MODEL = os.getenv("OPENAI_STT_MODEL", "gpt-4o-transcribe")





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

    `prompt` biases DECODING toward a vocabulary. It is OFF by default and
    should stay that way unless something forces the issue.

    HAZARD, observed in a live browser run: when the audio is unintelligible
    the model returns the PROMPT ITSELF as the transcript. That text then
    reaches the interviewer as the user's own words and lands in the
    AssessmentState — the exact fabrication the rest of the pipeline exists to
    prevent. The measured benefit was one casing fix ("N8N" -> "n8n"), which
    does not buy a route for invented assessment input.

    `_echoes_prompt` below is the backstop for any caller that decides the
    trade is worth it.
    """
    def call(client: OpenAI, endpoint):
        selected = endpoint.model or MODEL
        log.info("transcribe model=%s lang=%s filename=%s prompt_chars=%d",
                 selected, language, filename, len(prompt))
        resp = client.audio.transcriptions.create(
            model=selected,
            file=(filename, audio),
            language=language,
            **({"prompt": prompt} if prompt else {}),
        )
        record_usage(purpose="audio_transcription", model=selected,
                     usage=getattr(resp, "usage", None))
        return resp

    resp = execute("STT", call)
    text = resp.text if hasattr(resp, "text") else resp
    out = str(text).strip()
    if prompt and _echoes_prompt(out, prompt):
        log.warning("transcript echoed the prompt; discarded as unintelligible")
        return ""
    log.info("transcribe done chars=%d", len(out))
    return out


def _echoes_prompt(transcript: str, prompt: str) -> bool:
    """Is this transcript just the prompt handed back?

    Compared on word overlap rather than equality, because the model returns
    the prompt with its own punctuation and casing. An empty return is the
    honest answer: nothing was heard, so nothing is recorded.
    """
    def words(v: str) -> set[str]:
        return {w for w in "".join(c.lower() if c.isalnum() else " " for c in v).split() if w}

    said, hinted = words(transcript), words(prompt)
    if not said or not hinted:
        return False
    return len(said & hinted) / len(said) > 0.8
