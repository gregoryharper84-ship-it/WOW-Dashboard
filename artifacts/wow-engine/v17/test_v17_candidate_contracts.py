from pathlib import Path

import api_ncaaf_acceptance
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


def test_candidate_app_is_distinct_and_does_not_mutate_v16_route_table():
    assert api_v17_candidate.app is not api_ncaaf_acceptance.app
    v16_paths = {getattr(route, "path", None) for route in api_ncaaf_acceptance.app.router.routes}
    v17_paths = {getattr(route, "path", None) for route in api_v17_candidate.app.router.routes}
    assert "/score-team-event" not in v16_paths
    assert "/v17/host-contract" not in v16_paths
    assert "/score-team-event" in v17_paths
    assert "/v17/host-contract" in v17_paths


def test_candidate_app_preserves_v16_routes_and_lifecycle_hooks():
    paths = {getattr(route, "path", None) for route in api_v17_candidate.app.router.routes}
    assert "/score-prop" in paths
    assert "/score-pick-request" in paths
    assert "/governance" in paths
    assert "/record-recommendations" in paths
    assert "/settle-recommendations" in paths
    assert list(api_v17_candidate.app.router.on_startup) == list(api_ncaaf_acceptance.app.router.on_startup)
    assert list(api_v17_candidate.app.router.on_shutdown) == list(api_ncaaf_acceptance.app.router.on_shutdown)


def test_both_candidate_action_schemas_use_same_exact_render_origin():
    expected = "https://wow-governed-probability-engine.onrender.com"
    wow = WOW_SCHEMA.read_text()
    llp = LLP_SCHEMA.read_text()
    assert expected in wow
    assert expected in llp
    assert "REPLACE_WITH_RENDER_SERVICE_HOST" not in wow
    assert "REPLACE_WITH_RENDER_SERVICE_HOST" not in llp
    assert "CANDIDATE ONLY" in wow
    assert "CANDIDATE ONLY" in llp


def test_wow_action_has_prop_and_team_event_delegation():
    text = WOW_SCHEMA.read_text()
    ops = _operations(text)
    assert "scoreWowV17Prop" in ops
    assert "scoreWowV17PickRequest" in ops
    assert "scoreWowV17TeamEventFromWowHost" in ops
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


def test_both_action_contracts_preserve_no_execution_language():
    assert "can_execute is always false" in WOW_SCHEMA.read_text()
    assert "can_execute is always false" in LLP_SCHEMA.read_text()
