import os
from pathlib import Path

import api_v17_candidate


HERE = Path(__file__).parent
WOW_SCHEMA = HERE / "openapi.wow-betting-engine.v17.yaml"
LLP_SCHEMA = HERE / "openapi.llp-team-engine.v17.yaml"


def _operations(text: str) -> set[str]:
    return {
        line.split(":", 1)[1].strip()
        for line in text.splitlines()
        if line.strip().startswith("operationId:")
    }


def test_candidate_shadow_app_remains_distinct_harness_after_production_cutover():
    # The old Phase-A app remains useful as a shadow harness, but production
    # activation now occurs additively on api_ncaaf_acceptance under a flag.
    assert api_v17_candidate.app is not None
    paths = {getattr(route, "path", None) for route in api_v17_candidate.app.router.routes}
    assert "/score-team-event" in paths
    assert "/v17/host-contract" in paths


def test_candidate_shadow_app_preserves_governed_compatibility_routes():
    paths = {getattr(route, "path", None) for route in api_v17_candidate.app.router.routes}
    assert "/score-prop" in paths
    assert "/score-pick-request" in paths
    assert "/governance" in paths
    assert "/record-recommendations" in paths
    assert "/settle-recommendations" in paths


def test_both_v17_action_schemas_are_production_source_contracts_on_same_render_origin():
    expected = "https://wow-governed-probability-engine.onrender.com"
    wow = WOW_SCHEMA.read_text()
    llp = LLP_SCHEMA.read_text()
    assert expected in wow
    assert expected in llp
    assert "REPLACE_WITH_RENDER_SERVICE_HOST" not in wow
    assert "REPLACE_WITH_RENDER_SERVICE_HOST" not in llp
    assert "PRODUCTION SOURCE CONTRACT" in wow
    assert "PRODUCTION SOURCE CONTRACT" in llp
    assert "CANDIDATE ONLY" not in wow
    assert "CANDIDATE ONLY" not in llp
    assert "version: 17.0.0" in wow
    assert "version: 17.0.0" in llp


def test_wow_action_has_prop_and_team_event_delegation():
    text = WOW_SCHEMA.read_text()
    ops = _operations(text)
    assert "scoreWowV17Prop" in ops
    assert "scoreWowV17PickRequest" in ops
    assert "scoreWowV17TeamEventFromWowHost" in ops
    assert "runWowV17DailySnapshot" in ops
    assert "recordWowV17Recommendations" in ops
    assert "settleWowV17Recommendations" in ops
    assert "WOW_BETTING_ENGINE" in text
    assert "LLP_TEAM_BETTING_ENGINE" in text


def test_llp_action_has_team_event_but_no_prop_scoring_operation():
    text = LLP_SCHEMA.read_text()
    ops = _operations(text)
    assert "scoreLlpV17TeamEvent" in ops
    assert "recordLlpV17Recommendations" in ops
    assert "settleLlpV17Recommendations" in ops
    assert not any("Prop" in op for op in ops)
    assert "/score-prop" not in text
    assert "LLP_TEAM_BETTING_ENGINE" in text


def test_host_contract_requires_bearer_auth_in_both_production_schemas():
    wow = WOW_SCHEMA.read_text()
    llp = LLP_SCHEMA.read_text()
    assert "/v17/host-contract:" in wow and "security: [{actionBearer: []}]" in wow
    assert "/v17/host-contract:" in llp and "security: [{actionBearer: []}]" in llp


def test_both_action_contracts_preserve_no_execution_language():
    assert "can_execute is always false" in WOW_SCHEMA.read_text()
    assert "can_execute is always false" in LLP_SCHEMA.read_text()
