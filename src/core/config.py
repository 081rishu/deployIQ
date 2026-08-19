"""Environment-backed platform configuration."""

from __future__ import annotations

from dataclasses import dataclass
import os


def _origins(raw: str) -> tuple[str, ...]:
    origins = tuple(origin.strip() for origin in raw.split(",") if origin.strip())
    if "*" in origins:
        raise RuntimeError(
            "DEPLOYIQ_ALLOWED_ORIGINS cannot contain '*' when credentials are enabled"
        )
    return origins


@dataclass(frozen=True)
class Settings:
    environment: str
    log_level: str
    allowed_origins: tuple[str, ...]

    @classmethod
    def from_env(cls) -> "Settings":
        environment = os.getenv("DEPLOYIQ_ENV", "development")
        raw_origins = os.getenv("DEPLOYIQ_ALLOWED_ORIGINS")
        # Explicit production allowlists remain mandatory. Local browser
        # development is safe and usable out of the box without a wildcard.
        if raw_origins is None and environment == "development":
            raw_origins = "http://localhost:3000,http://127.0.0.1:3000"
        return cls(
            environment=environment,
            log_level=os.getenv("DEPLOYIQ_LOG_LEVEL", os.getenv("LOG_LEVEL", "INFO")).upper(),
            allowed_origins=_origins(raw_origins or ""),
        )
