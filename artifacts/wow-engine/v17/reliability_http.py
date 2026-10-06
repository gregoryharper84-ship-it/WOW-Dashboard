"""Observable HTTP safety invariants for the governed WOW production app."""
from __future__ import annotations

from typing import Any

DRY_RUN_HEADER = "X-WOW-Dry-Run-Only"
CAN_EXECUTE_HEADER = "X-WOW-Can-Execute"
_INSTALL_ATTR = "_wow_reliability_headers_installed"


def install_reliability_headers(app: Any) -> bool:
    """Install idempotent safety headers on every HTTP response."""
    if getattr(app.state, _INSTALL_ATTR, False):
        return True

    @app.middleware("http")
    async def _wow_reliability_headers(request, call_next):
        response = await call_next(request)
        response.headers[DRY_RUN_HEADER] = "true"
        response.headers[CAN_EXECUTE_HEADER] = "false"
        return response

    setattr(app.state, _INSTALL_ATTR, True)
    return True


__all__ = [
    "CAN_EXECUTE_HEADER",
    "DRY_RUN_HEADER",
    "install_reliability_headers",
]
