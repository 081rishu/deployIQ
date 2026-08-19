"""Package-resource paths for DeployIQ runtime assets."""

from __future__ import annotations

from importlib.resources import files
from pathlib import Path

_ASSETS = files("deployiq_assets")


def _asset_path(*parts: str) -> Path:
    # Wheels are installed as ordinary filesystem trees in supported backend
    # deployments, so FileResponse and JSON loaders receive concrete paths.
    return Path(_ASSETS.joinpath(*parts))


def data_path(*parts: str) -> Path:
    return _asset_path("data", *parts)


def static_path(*parts: str) -> Path:
    return _asset_path("static", *parts)
