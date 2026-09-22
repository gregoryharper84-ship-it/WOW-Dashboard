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
from v17.core_intelligence_compounding_routes import install_compounding_intelligence_routes_read_only
from v17.core_intelligence_event_runtime import install_core_intelligence_event_routes
from v17.core_intelligence_runtime import install_core_intelligence_routes
from v17.core_intelligence_shadow_runtime import install_shadow_lab_routes
from v17.llp_v17_1_shadow_runtime import install_llp_v17_1_shadow_routes
from v17.llp_v17_1_shadow_scorecard_runtime import install_llp_v17_1_shadow_scorecard_routes
# Import through the V17 preservation shim so downstream LLP governance holds
# cannot erase a completed fitted sporting probability. The shim preserves all
# rank/publication/terminal gates and can_execute=false.
from v17.team_event_probability_preservation import install_team_event_routes

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

# Core Intelligence installers expect the underlying callable so they can wrap it
# in FastAPI Depends. Passing v16._auth (already a Depends object) would otherwise
# skip dependency installation and expose these routes without the Action-key gate.
_core_intelligence_auth = v16.base.market_api.prod._require_action_api_key

# Core Intelligence is out-of-band learning only. It reads immutable predictions
# and authoritative outcomes, writes append-only evidence, and cannot change the
# V17 scoring terminal or execute a wager.
install_core_intelligence_routes(
    app,
    auth_dependency=_core_intelligence_auth,
    get_client_fn=v16._db_client,
)
install_core_intelligence_event_routes(
    app,
    auth_dependency=_core_intelligence_auth,
    get_client_fn=v16._db_client,
)
install_compounding_intelligence_routes_read_only(
    app,
    auth_dependency=_core_intelligence_auth,
    get_client_fn=v16._db_client,
)
install_shadow_lab_routes(
    app,
    auth_dependency=_core_intelligence_auth,
    get_client_fn=v16._db_client,
)
# LLP V17.1 shadow capture/grade is deliberately out-of-band. It persists
# side-specific challenger evidence only and cannot alter production scoring,
# calibration, ranking, terminal authority, or execution posture.
install_llp_v17_1_shadow_routes(
    app,
    auth_dependency=_core_intelligence_auth,
    get_client_fn=v16._db_client,
)
# Evaluation is read-only over the immutable shadow/grade ledgers. It compares
# ranking objectives and cohorts but cannot auto-promote a challenger.
install_llp_v17_1_shadow_scorecard_routes(
    app,
    auth_dependency=_core_intelligence_auth,
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
