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

from core.costs import record_usage

# Load the key from .env (README/.env.example), with .env.local taking
# precedence for a local override. Both are gitignored; load_dotenv does not
# overwrite already-set variables, so the first file wins.
load_dotenv(".env.local")
load_dotenv(".env")

MODEL = os.getenv("OPENAI_MODEL", "gpt-4o")


def _client() -> OpenAI:
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        raise RuntimeError(
            "OPENAI_API_KEY is not set — copy .env.example to .env and fill it in"
        )
    return OpenAI(api_key=api_key)


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
    client = _client()
    selected_model = model or MODEL
    resp = client.chat.completions.create(
        model=selected_model,
        temperature=temperature,
        response_format={"type": "json_object"},
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
    )
    record_usage(purpose="chat_json", model=selected_model,
                 usage=getattr(resp, "usage", None))
    content = resp.choices[0].message.content or "{}"
    return json.loads(content)


def complete_text(
    system: str,
    user: str,
    *,
    temperature: float = 0.3,
    model: Optional[str] = None,
) -> str:
    client = _client()
    selected_model = model or MODEL
    resp = client.chat.completions.create(
        model=selected_model,
        temperature=temperature,
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
    )
    record_usage(purpose="chat_text", model=selected_model,
                 usage=getattr(resp, "usage", None))
    return resp.choices[0].message.content or ""
