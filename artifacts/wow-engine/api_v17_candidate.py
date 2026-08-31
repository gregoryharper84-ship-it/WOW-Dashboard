"""WOW v17 candidate API wrapper.

This module is intentionally NOT the production Render entrypoint during Phase A.
It composes the accepted v16 governed routes into a distinct FastAPI app, then
adds only candidate v17 host/team-event contracts for shadow/acceptance testing.
Importing this module must not mutate the accepted v16 app. No live wager
execution is possible.
"""
from __future__ import annotations

import json
from pathlib import Path

from fastapi import FastAPI

import api_ncaaf_acceptance as v16
from v17.team_event_request_runtime import install_team_event_routes

app = FastAPI(
    title="WOW v17 Candidate Governed Core",
    version="17.0.0-phase-a",
    docs_url=None,
    redoc_url=None,
    openapi_url=None,
)
app.router.routes.extend(list(v16.app.router.routes))
app.exception_handlers.update(v16.app.exception_handlers)

install_team_event_routes(
    app,
    event_api=v16.base.market_api.prod.event_api,
    auth_dependency=v16._auth,
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
        "v17_candidate_implementation": payload["v17_candidate_implementation"],
        "editor_attestation": payload["editor_attestation"],
        "resolved_phase_a_findings": payload["resolved_phase_a_findings"],
        "phase_a_blockers": payload["phase_a_blockers"],
        "can_execute": False,
    }
