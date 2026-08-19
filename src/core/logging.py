"""Structured, request-aware application logging."""

from __future__ import annotations

import json
import logging
import sys
from datetime import datetime, timezone

from core.config import Settings
from core.request_context import get_request_id

_configured = False


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        request_id = get_request_id()
        if request_id:
            payload["request_id"] = request_id
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False)


def configure_logging(settings: Settings | None = None) -> None:
    global _configured
    if _configured:
        return
    settings = settings or Settings.from_env()
    root = logging.getLogger()
    if not any(getattr(handler, "_deployiq_handler", False)
               for handler in root.handlers):
        handler = logging.StreamHandler(sys.stdout)
        handler._deployiq_handler = True
        handler.setFormatter(JsonFormatter())
        root.addHandler(handler)
    root.setLevel(settings.log_level)
    for third_party in ("httpx", "httpx2", "uvicorn.access"):
        logging.getLogger(third_party).setLevel(logging.WARNING)
    _configured = True


def get_logger(name: str) -> logging.Logger:
    configure_logging()
    return logging.getLogger(name)
