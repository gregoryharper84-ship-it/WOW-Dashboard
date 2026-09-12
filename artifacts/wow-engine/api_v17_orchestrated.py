"""Production V17 entrypoint with consolidated Daily Pick orchestration.

This wrapper reuses the accepted governed production app and adds only the V17
control-plane endpoint. It does not replace any fitted specialist, probability
math, calibration, publication guard, or terminal authority.
"""
from __future__ import annotations

import api_ncaaf_acceptance as base
from v17.daily_pick_orchestrator import install_daily_pick_orchestrator_route

app = base.app

if base.V17_ACTIVE:
    install_daily_pick_orchestrator_route(
        app,
        auth_dependency=base._auth,
        db_client_fn=base._db_client,
        market_api=base.base.market_api,
        event_api=base.base.market_api.prod.event_api,
    )

# api_ncaaf_acceptance may have materialized the OpenAPI cache before this
# wrapper mounted the final route. Invalidate only the cache, not the app.
app.openapi_schema = None
