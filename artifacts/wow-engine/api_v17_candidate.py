"""WOW v17 candidate API wrapper.

This module is intentionally NOT the production Render entrypoint during Phase A.
It composes the accepted v16 governed routes into a distinct FastAPI app, then
adds only candidate v17 contracts for shadow/acceptance testing. Importing this
module must not mutate the accepted v16 app. No live wager execution is possible.
"""
from __future__ import annotations

import json
from pathlib import Path

from fastapi import FastAPI

import api_ncaaf_acceptance as v16
from recommendation_ledger_api import install_recommendation_ledger_routes
from v17.team_event_request_runtime import install_team_event_routes

app = FastAPI(
    title="WOW v17 Candidate Governed Core",
    version="17.0.0-phase-a",
    docs_url=None,
    redoc_url=None,
    openapi_url=None,
    on_startup=list(v16.app.router.on_startup),
    on_shutdown=list(v16.app.router.on_shutdown),
)
app.router.routes.extend(list(v16.app.router.routes))
app.exception_handlers.update(v16.app.exception_handlers)

install_team_event_routes(
    app,
    event_api=v16.base.market_api.prod.event_api,
    auth_dependency=v16._auth,
)

# The legacy V16 compatibility layer contains the audited ledger implementation,
# but the accepted production wrapper does not mount these routes. Mount them
# explicitly here so this shadow harness matches both active V17 Action schemas.
install_recommendation_ledger_routes(
    app,
    auth_dependency=v16._auth,
    get_client_fn=v16._db_client,
)


@app.get("/v17/host-contract", operation_id="getWowV17HostContract")
def get_v17_host_contract():
    contract_path = Path(__file__).with_name("v17") / "custom_engine_alignment_contract.json"
    payload = json.loads(contract_path.read_text())
    return {
        "schema_version": payload["schema_version"],
        "status": payload["status"],
        "activation": payload["activation"],
        "hosts": payload["hosts"],
        "shared_core": payload["shared_core"],
        "team_event_contract": payload["team_event_contract"],
        "prop_contract": payload["prop_contract"],
        "v17_active_implementation": payload["v17_active_implementation"],
        "editor_attestation": payload["editor_attestation"],
        "resolved_phase_a_findings": payload["resolved_phase_a_findings"],
        "phase_a_blockers": payload["phase_a_blockers"],
        "can_execute": False,
    }
