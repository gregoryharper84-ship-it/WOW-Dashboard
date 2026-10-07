from __future__ import annotations

from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1] / "v17"
LLP_SCHEMA = ROOT / "openapi.llp-team-engine.v17.yaml"
WOW_SCHEMA = ROOT / "openapi.wow-betting-engine.v17.yaml"
LLP_GATEWAY = ROOT / "supabase" / "functions" / "wow-llp-action-gateway" / "index.ts"


def _documents() -> tuple[dict, dict]:
    return yaml.safe_load(LLP_SCHEMA.read_text()), yaml.safe_load(WOW_SCHEMA.read_text())


def _market_component(request: dict) -> dict:
    assert "market_input" not in request["required"]
    return request["properties"]["market_input"]


def test_both_live_team_event_routes_reference_market_input_capable_schema():
    llp, wow = _documents()
    llp_path = "/functions/v1/wow-llp-action-gateway/score-team-event"
    wow_path = "/score-team-event"

    assert (
        llp["paths"][llp_path]["post"]["requestBody"]["content"]["application/json"]["schema"]["$ref"]
        == "#/components/schemas/LlpTeamEventRequest"
    )
    assert (
        wow["paths"][wow_path]["post"]["requestBody"]["content"]["application/json"]["schema"]["$ref"]
        == "#/components/schemas/TeamEventRequest"
    )

    llp_market = _market_component(llp["components"]["schemas"]["LlpTeamEventRequest"])
    wow_market = _market_component(wow["components"]["schemas"]["TeamEventRequest"])
    for market in (llp_market, wow_market):
        assert market["additionalProperties"] is False
        assert market["required"] == ["market_family", "selection"]
        assert market["properties"]["market_family"]["enum"] == ["MONEYLINE"]
        assert market["properties"]["selection"]["type"] == "string"
        assert market["properties"]["american_odds"]["type"] == ["number", "null"]
        assert "implied_probability" not in market["properties"]


def test_market_input_contract_explicitly_denies_probability_authority():
    llp, wow = _documents()
    markets = (
        llp["components"]["schemas"]["LlpTeamEventRequest"]["properties"]["market_input"],
        wow["components"]["schemas"]["TeamEventRequest"]["properties"]["market_input"],
    )
    for market in markets:
        description = " ".join(str(market["description"]).split()).lower()
        assert "never" in description
        assert "governed sporting probability" in description
        assert "substitutes" in description
        assert "market/value qualification remains separate" in description


def test_action_schema_versions_advance_for_market_input():
    llp, wow = _documents()
    assert llp["info"]["version"] == "17.0.5-free-core-market-input"
    assert wow["info"]["version"] == "17.0.1-free-core-market-input"



def test_llp_gateway_forwards_score_team_event_body_without_field_projection():
    text = LLP_GATEWAY.read_text()
    assert '{ method: "POST", pattern: /^\\/score-team-event$/, auth: true }' in text
    assert 'upstreamPath === "/score-team-event"' not in text
    assert "await req.arrayBuffer()" in text
    assert "body," in text
    assert "market_input" not in text
