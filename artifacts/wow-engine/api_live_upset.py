"""Final WOW production entrypoint with governed LIVE_UPSET routes installed.

This wrapper intentionally layers on top of api_ncaaf_acceptance so every
post-PR-84 calibration/publication policy remains authoritative.
"""
import api_ncaaf_acceptance as base
from live_probability_runtime import install_live_probability_routes

app = base.app
install_live_probability_routes(
    app,
    auth_dependency=base._auth,
    db_client_fn=base._db_client,
)
app.openapi_schema = None
