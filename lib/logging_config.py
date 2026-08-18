"""Centralized logging configuration.

Provides a get_logger() helper so every module logs consistently with
structured context (module name) and a sane default format. Level is
configurable via the LOG_LEVEL env var (default INFO).
"""

from __future__ import annotations

import logging
import os

_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()


def get_logger(name: str) -> logging.Logger:
    logger = logging.getLogger(name)
    if not logger.handlers:
        handler = logging.StreamHandler()
        handler.setFormatter(
            logging.Formatter(
                "%(asctime)s | %(levelname)s | %(name)s | %(message)s"
            )
        )
        logger.addHandler(handler)
        logger.setLevel(_LEVEL)
        logger.propagate = False
    return logger
