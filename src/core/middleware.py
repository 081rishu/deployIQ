"""HTTP observability and safe unexpected-error responses."""

from __future__ import annotations

from time import perf_counter
from uuid import uuid4

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from core.logging import get_logger
from core.request_context import get_request_id, reset_request_id, set_request_id

log = get_logger("api.request")


def install_api_observability(app: FastAPI) -> None:
    @app.middleware("http")
    async def request_context(request: Request, call_next):
        request_id = request.headers.get("X-Request-ID") or str(uuid4())
        token = set_request_id(request_id)
        started = perf_counter()
        try:
            response = await call_next(request)
        except Exception:
            log.exception("unhandled_api_exception")
            response = JSONResponse(
                status_code=500,
                content={
                    "detail": "Internal server error",
                    "request_id": request_id,
                },
            )
        finally:
            elapsed_ms = round((perf_counter() - started) * 1000, 2)
            log.info(
                "request_completed method=%s path=%s elapsed_ms=%s",
                request.method,
                request.url.path,
                elapsed_ms,
            )
            reset_request_id(token)
        response.headers["X-Request-ID"] = request_id
        return response
