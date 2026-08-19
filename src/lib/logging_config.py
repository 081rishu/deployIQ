"""Backward-compatible logging import for existing domain modules.

Platform logging now lives in ``core.logging``. Keep this adapter so frozen
analytical imports remain stable while configuration and observability are
centralized outside domain code.
"""

from core.logging import get_logger

__all__ = ["get_logger"]
