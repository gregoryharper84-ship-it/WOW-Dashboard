import os
from pathlib import Path

import yaml
import api_v17_candidate


HERE = Path(__file__).parent
WOW_SCHEMA = HERE / "openapi.wow-betting-engine.v17.yaml"
LLP_SCHEMA = HERE / "openapi.llp-team-engine.v17.yaml"
LLP_INSTRUCTIONS = HERE.parent / "LLP_V17_CUSTOM_GPT_INSTRUCTIONS.txt"
ROOT_LLP_INSTRUCTIONS = HERE.parents[2] / "LLP-TEAM-BETTING-GPT-INSTRUCTIONS.md"
LLP_GATEWAY = HERE / "supabase/functions/wow-llp-action-gateway/index.ts"
LLP_SUPABASE_CONFIG = HERE / "supabase/config.toml"


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


def test_v17_action_schemas_preserve_backend_and_llp_gateway_transport_contracts():
    render_origin = "https://wow-governed-probability-engine.onrender.com"
    llp_gateway_origin = (
        "https://iczfhsmjrrafhvcpmqhr.supabase.co/functions/v1/wow-llp-action-gateway"
    )
    wow = WOW_SCHEMA.read_text()
    llp = LLP_SCHEMA.read_text()
    assert render_origin in wow
    assert llp_gateway_origin in llp
    assert "closed Supabase Edge" in llp
    assert "unchanged\n    governed Render runtime" in llp
    assert "transport-only" in llp
    assert "REPLACE_WITH_RENDER_SERVICE_HOST" not in wow
    assert "REPLACE_WITH_RENDER_SERVICE_HOST" not in llp
    assert "PRODUCTION SOURCE CONTRACT" in wow
    assert "PRODUCTION SOURCE CONTRACT" in llp
    assert "CANDIDATE ONLY" not in wow
    assert "CANDIDATE ONLY" not in llp
    assert "version: 17.0.0" in wow
    assert "version: 17.0.1-transport-gateway" in llp


def test_wow_action_has_prop_and_team_event_delegation():
    text = WOW_SCHEMA.read_text()
    ops = _operations(text)
    assert "scoreWowProp" in ops
    assert "scoreWowPickRequest" in ops
    assert "scoreWowV17TeamEventFromWowHost" in ops
    assert "runWowV17DailySnapshot" in ops
    assert "recordWowV17Recommendations" in ops
    assert "settleWowV17Recommendations" in ops
    assert "WOW_BETTING_ENGINE" in text
    assert "LLP_TEAM_BETTING_ENGINE" in text


def test_llp_action_has_team_event_and_line_shadows_but_no_prop_scoring_operation():
    text = LLP_SCHEMA.read_text()
    ops = _operations(text)
    assert len(ops) == 12
    assert "runLlpV17FullSlate" in ops
    assert "readLlpV17FullSlateRows" in ops
    assert "scoreLlpV17TeamEvent" in ops
    assert "scoreLlpV17SpreadForwardShadow" in ops
    assert "scoreLlpV17NFLSpreadForwardShadow" in ops
    assert "scoreLlpV17WNBASpreadForwardShadow" in ops
    assert "scoreLlpV17MLBRunLineForwardShadow" in ops
    assert "recordLlpV17Recommendations" in ops
    assert "settleLlpV17Recommendations" in ops
    assert not any("Prop" in op for op in ops)
    assert "/score-prop" not in text
    assert "LLP_TEAM_BETTING_ENGINE" in text

    full_slate_route = text[text.index("  /v17/daily-snapshot-run:"):text.index("  /score-team-event:")]
    assert "operationId: runLlpV17FullSlate" in full_slate_route
    assert "items: {type: string, enum: [MONEYLINE]}" in text
    assert "max_props: {type: integer, enum: [0]}" in text

    route = text[text.index("  /internal/v17/spread-forward-shadow:"):text.index("  /record-recommendations:")]
    assert "operationId: scoreLlpV17SpreadForwardShadow" in route
    assert "operationId: scoreLlpV17NFLSpreadForwardShadow" in route
    assert "operationId: scoreLlpV17WNBASpreadForwardShadow" in route
    assert "operationId: scoreLlpV17MLBRunLineForwardShadow" in route
    assert route.count("security: [{actionBearer: []}]") == 4
    assert "Research-only NCAAF exact-line spread forward shadow" in route
    assert "Research-only NFL exact-line spread forward shadow" in route
    assert "Research-only WNBA exact-line spread forward shadow" in route
    assert "Research-only MLB exact run-line forward shadow" in route
    assert "no moneyline-to-spread" in route
    assert "no market-probability substitution" in route

    ncaaf_request = text[text.index("    SpreadForwardShadowRequest:"):text.index("    NFLSpreadForwardShadowRequest:")]
    assert "additionalProperties: false" in ncaaf_request
    assert "required: [sport, event_id, event_start_time, home_team, away_team, home_spread, season]" in ncaaf_request
    assert "sport: {type: string, enum: [NCAAF]}" in ncaaf_request

    nfl_request = text[text.index("    NFLSpreadForwardShadowRequest:"):text.index("    WNBASpreadForwardShadowRequest:")]
    assert "required: [sport, event_id, event_start_time, home_team, away_team, home_spread]" in nfl_request
    assert "sport: {type: string, enum: [NFL]}" in nfl_request

    wnba_request = text[text.index("    WNBASpreadForwardShadowRequest:"):text.index("    MLBRunLineForwardShadowRequest:")]
    assert "required: [sport, event_id, event_start_time, home_team_id, away_team_id, home_spread]" in wnba_request
    assert "sport: {type: string, enum: [WNBA]}" in wnba_request
    assert "pattern: '^espn-.+$'" in wnba_request

    mlb_request = text[text.index("    MLBRunLineForwardShadowRequest:"):text.index("    RecommendationBatch:")]
    assert "required: [sport, score_snapshot_id, home_run_line]" in mlb_request
    assert "sport: {type: string, enum: [MLB]}" in mlb_request

    team_request = text[text.index("    LlpTeamEventRequest:"):]
    assert "market_family: {type: string, enum: [OUTRIGHT_WINNER]}" in team_request


def test_llp_instructions_fit_editor_limit_and_preserve_spread_governance():
    text = LLP_INSTRUCTIONS.read_text()
    assert len(text) <= 8000
    assert len(text.encode("utf-8")) <= 7500
    assert "point spreads" in text
    assert "scoreLlpV17SpreadForwardShadow" in text
    assert "no ML->spread or market-probability substitution" in text
    assert "exact spread as post-fit threshold only" in text
    assert "can_execute=false" in text


def test_root_llp_authority_block_cannot_drift_from_spread_action_contract():
    text = ROOT_LLP_INSTRUCTIONS.read_text()
    blocks = text.split("```")
    assert len(blocks) >= 3
    authority_block = blocks[1]
    assert len(authority_block) <= 8000
    assert "scoreLlpV17SpreadForwardShadow" in authority_block
    assert "POINT_SPREAD" in authority_block
    assert "POINT-SPREAD LANE" in authority_block
    assert "p_cover, p_push, p_not_cover" in authority_block
    assert "prediction_authority=false" in authority_block
    assert "exact-line acquisition is not completion" in authority_block
    assert "home_spread,season" in authority_block
    assert "never infer cover probability from market/ML" in authority_block
    assert "V17_TERMINAL_REDUCER" in authority_block
    assert "can_execute=false" in authority_block


def test_host_contract_requires_bearer_auth_in_both_production_schemas():
    wow = WOW_SCHEMA.read_text()
    llp = LLP_SCHEMA.read_text()
    assert "/v17/host-contract:" in wow and "security: [{actionBearer: []}]" in wow
    assert "/v17/host-contract:" in llp and "security: [{actionBearer: []}]" in llp


def test_both_action_contracts_preserve_no_execution_language():
    assert "can_execute is always false" in WOW_SCHEMA.read_text()
    assert "can_execute is always false" in LLP_SCHEMA.read_text()


def test_declared_combat_sports_terminate_as_model_unavailable():
    # #337 requires MMA/Boxing to be declared and to stay MODEL_UNAVAILABLE.
    # Declaration must never become capability.
    from v17.team_event_capability_manifest import (
        CERTIFIED_TEAM_EVENT_SPORTS,
        EXPECTED_TEAM_EVENT_SPORTS,
        TEAM_EVENT_INPUT_CONTRACTS,
        team_event_capability,
    )

    for sport in ("MMA", "BOXING"):
        assert sport in EXPECTED_TEAM_EVENT_SPORTS
        assert sport in TEAM_EVENT_INPUT_CONTRACTS
        assert sport not in CERTIFIED_TEAM_EVENT_SPORTS
        capability = team_event_capability(sport)
        assert capability.status == "MODEL_UNAVAILABLE"
        assert capability.controlling_specialist is None
        assert capability.blocker == "TEAM_EVENT_SPECIALIST_ARTIFACT_NOT_CERTIFIED"
        assert capability.can_execute is False
        # A combat bout can end without a winner; the outcome space must say so.
        assert "no_contest_draw_outcome_space" in capability.required_inputs


def test_combat_sport_aliases_resolve_without_borrowing_a_certified_model():
    from v17.team_event_capability_manifest import team_event_capability

    for alias in ("UFC", "Mixed Martial Arts", "mma"):
        assert team_event_capability(alias).sport == "MMA"
    assert team_event_capability("BOX").sport == "BOXING"
    for alias in ("UFC", "BOX"):
        assert team_event_capability(alias).controlling_specialist is None


def test_only_mlb_remains_certified():
    from v17.team_event_capability_manifest import CERTIFIED_TEAM_EVENT_SPORTS

    assert set(CERTIFIED_TEAM_EVENT_SPORTS) == {"MLB"}

def test_llp_supabase_gateway_diagnostic_contract_is_narrow():
    diagnostic = yaml.safe_load(
        (HERE / "openapi.llp-team-engine.v17.supabase-gateway-diagnostic.yaml").read_text()
    )
    assert diagnostic["servers"] == [{
        "url": "https://iczfhsmjrrafhvcpmqhr.supabase.co/functions/v1/wow-llp-action-gateway"
    }]
    assert set(diagnostic["paths"]) == {
        "/health",
        "/internal/v17/spread-forward-shadow",
    }
    spread = diagnostic["paths"]["/internal/v17/spread-forward-shadow"]["post"]
    assert spread["operationId"] == "scoreLlpV17SpreadForwardShadow"
    assert spread["security"] == [{"actionBearer": []}]
    schema = diagnostic["components"]["schemas"]["SpreadForwardShadowRequest"]
    assert schema["properties"]["sport"]["const"] == "NCAAF"
    assert set(schema["required"]) == {
        "sport", "event_id", "event_start_time", "home_team",
        "away_team", "home_spread", "season",
    }

def test_llp_supabase_gateway_covers_only_canonical_action_routes():
    source = LLP_GATEWAY.read_text()
    canonical_routes = {
        "/health",
        "/governance",
        "/v17/host-contract",
        "/v17/daily-snapshot-run",
        "/score-team-event",
        "/internal/v17/spread-forward-shadow",
        "/internal/v17/nfl-spread-forward-shadow",
        "/internal/v17/wnba-spread-forward-shadow",
        "/internal/v17/mlb-run-line-forward-shadow",
        "/record-recommendations",
        "/settle-recommendations",
    }
    for route in canonical_routes:
        assert route.replace("/", "\\/") in source
    assert r"^\/v17\/daily-snapshot-run\/[^/]+\/rows$" in source
    assert "LLP_GATEWAY_PATH_NOT_ALLOWED" in source
    assert "LLP_GATEWAY_METHOD_NOT_ALLOWED" in source
    assert "LLP_GATEWAY_AUTH_REQUIRED" in source
    assert 'authorization.startsWith("Bearer ")' in source
    assert 'headers.set("authorization", authorization)' in source
    assert "upstream.search = url.search" in source
    assert "redirect: \"manual\"" in source
    assert "can_execute: false" in source
    assert "TARGET + upstreamPath" in source
    assert "req.headers" not in source.split('headers.set("user-agent"')[1]


def test_llp_gateway_supabase_platform_jwt_check_is_disabled_for_wow_bearer():
    config = LLP_SUPABASE_CONFIG.read_text()
    assert "[functions.wow-llp-action-gateway]" in config
    assert "verify_jwt = false" in config

