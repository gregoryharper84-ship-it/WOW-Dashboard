"""Final WOW production entrypoint with governed LIVE_UPSET routes installed."""
import api_ncaaf_acceptance as base
from live_probability_runtime import install_live_probability_routes

app = base.app
install_live_probability_routes(app, auth_dependency=base._auth, db_client_fn=base._db_client)
app.openapi_schema = None
