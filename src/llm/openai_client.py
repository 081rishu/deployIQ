"""Thin wrapper around the OpenAI SDK.

Server-side only (called from FastAPI endpoints / the interviewer engine).

Exposes a single helper that returns structured JSON, so the engine never
parses free text.
"""

from __future__ import annotations

import json
import os
from typing import Any, Optional

from dotenv import load_dotenv
from openai import OpenAI

from llm.provider import execute

from core.costs import record_usage

# Load the key from .env (README/.env.example), with .env.local taking
# precedence for a local override. Both are gitignored; load_dotenv does not
# overwrite already-set variables, so the first file wins.
load_dotenv(".env.local")
load_dotenv(".env")

MODEL = os.getenv("OPENAI_MODEL", "gpt-4o")





def complete_json(
    system: str,
    user: str,
    *,
    temperature: float = 0.2,
    model: Optional[str] = None,
) -> dict[str, Any]:
    """Return a JSON object from the model.

    Uses JSON-mode response_format so the result is always parseable.
    """
    def call(client: OpenAI, endpoint_model: Optional[str]):
        selected = model or endpoint_model or MODEL
        resp = client.chat.completions.create(
            model=selected,
            temperature=temperature,
            response_format={"type": "json_object"},
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        )
        record_usage(purpose="chat_json", model=selected,
                     usage=getattr(resp, "usage", None))
        return json.loads(resp.choices[0].message.content or "{}")

    return execute("LLM", call)


def complete_text(
    system: str,
    user: str,
    *,
    temperature: float = 0.3,
    model: Optional[str] = None,
) -> str:
    def call(client: OpenAI, endpoint_model: Optional[str]) -> str:
        selected = model or endpoint_model or MODEL
        resp = client.chat.completions.create(
            model=selected,
            temperature=temperature,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        )
        record_usage(purpose="chat_text", model=selected,
                     usage=getattr(resp, "usage", None))
        return resp.choices[0].message.content or ""

    return execute("LLM", call)
