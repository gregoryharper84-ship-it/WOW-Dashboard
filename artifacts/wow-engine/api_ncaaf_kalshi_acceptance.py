"""Accepted governed production app plus opt-in Kalshi Weather V2 shadow routes.

This wrapper does not replace or mutate any sports scoring authority. It imports
`api_ncaaf_acceptance.app` and mounts a separate Kalshi namespace only when the
feature flag is enabled. The routes reuse the existing backend-only Supabase
service credential and Action bearer authentication.
"""
from __future__ import annotations

import logging
import os

import api_ncaaf_acceptance as base
from kalshi_weather_v2.routes import install_kalshi_weather_v2_routes


app = base.app
KALSHI_WEATHER_V2_ACTIVE = os.getenv("WOW_KALSHI_WEATHER_V2_ACTIVE", "0") == "1"
_logger = logging.getLogger("wow.kalshi_weather_v2.activation")

if KALSHI_WEATHER_V2_ACTIVE:
    install_kalshi_weather_v2_routes(
        app,
        auth_dependency=base._auth,
        db_client_fn=base._db_client,
    )


@app.on_event("startup")
async def log_kalshi_weather_v2_activation():
    _logger.warning(
        "WOW_KALSHI_WEATHER_V2_RUNTIME status=%s mode=SHADOW probability_publishable=false can_execute=false",
        "ACTIVE" if KALSHI_WEATHER_V2_ACTIVE else "INACTIVE",
    )
