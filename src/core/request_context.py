"""Request-scoped correlation context for API observability."""

from __future__ import annotations

from contextvars import ContextVar
from typing import Optional

_request_id: ContextVar[Optional[str]] = ContextVar("deployiq_request_id", default=None)


def get_request_id() -> Optional[str]:
    return _request_id.get()


def set_request_id(value: str):
    return _request_id.set(value)


def reset_request_id(token) -> None:
    _request_id.reset(token)
