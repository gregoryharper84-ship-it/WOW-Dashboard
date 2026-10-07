from __future__ import annotations

from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1] / "v17"
SCHEMAS = (
    ROOT / "openapi.llp-team-engine.v17.yaml",
    ROOT / "openapi.wow-betting-engine.v17.yaml",
)


def _team_request(path: Path) -> dict:
    document = yaml.safe_load(path.read_text())
    return document["components"]["schemas"]["TeamEventRequest"]


def test_both_action_contracts_expose_optional_moneyline_market_input():
    for path in SCHEMAS:
        request = _team_request(path)
        assert "market_input" not in request["required"]
        market = request["properties"]["market_input"]
        assert market["additionalProperties"] is False
        assert market["required"] == ["market_family", "selection"]
        assert market["properties"]["market_family"]["enum"] == ["MONEYLINE"]
        assert market["properties"]["selection"]["type"] == "string"
        assert market["properties"]["american_odds"]["type"] == ["number", "null"]
        assert "implied_probability" not in market["properties"]


def test_market_input_contract_explicitly_denies_probability_authority():
    for path in SCHEMAS:
        market = _team_request(path)["properties"]["market_input"]
        description = " ".join(str(market["description"]).split()).lower()
        assert "never" in description
        assert "governed sporting probability" in description
        assert "substitutes" in description
        assert "market/value qualification remains separate" in description


def test_action_schema_versions_advance_for_market_input():
    llp = yaml.safe_load(SCHEMAS[0].read_text())
    wow = yaml.safe_load(SCHEMAS[1].read_text())
    assert llp["info"]["version"] == "17.0.5-free-core-market-input"
    assert wow["info"]["version"] == "17.0.1-free-core-market-input"
