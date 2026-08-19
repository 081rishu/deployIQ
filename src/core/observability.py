"""Safe stage-level observability for transport and orchestration boundaries."""

from __future__ import annotations

from contextlib import contextmanager
from time import perf_counter
from typing import Callable, Iterator, TypeVar

from core.logging import get_logger

T = TypeVar("T")
log = get_logger("deployiq.stage")


@contextmanager
def stage(name: str) -> Iterator[None]:
    started = perf_counter()
    log.info("stage_started name=%s", name)
    try:
        yield
    except Exception:
        log.exception("stage_failed name=%s", name)
        raise
    else:
        log.info("stage_completed name=%s elapsed_ms=%s", name,
                 round((perf_counter() - started) * 1000, 2))


def run_stage(name: str, operation: Callable[..., T], *args, **kwargs) -> T:
    with stage(name):
        return operation(*args, **kwargs)
