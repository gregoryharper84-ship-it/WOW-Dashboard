"""Authenticated read-only provider health routes for V17."""
from __future__ import annotations

from typing import Any

from fastapi import Depends, FastAPI

from v17.fallback_provider_health import probe_odds_api_health

CAN_EXECUTE = False


def install_fallback_provider_health_routes(
    app: FastAPI,
    *,
    auth_dependency: Any | None = None,
) -> bool:
    if getattr(app.state, "v17_fallback_provider_health_routes_installed", False):
        return True
    dependencies = [Depends(auth_dependency)] if callable(auth_dependency) else []

    @app.get(
        "/v17/market-health/odds-api",
        operation_id="getV17OddsApiMarketHealth",
        dependencies=dependencies,
    )
    def get_v17_odds_api_market_health():
        return probe_odds_api_health()

    app.state.v17_fallback_provider_health_routes_installed = True
    return True


__all__ = ["CAN_EXECUTE", "install_fallback_provider_health_routes"]
