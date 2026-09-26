from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[3]
ENGINE = ROOT / "artifacts" / "wow-engine"
SCHEMA = ENGINE / "v17" / "openapi.wow-betting-engine.v17.yaml"
SYNC_BUILDER = ENGINE / "v17" / "build_gpt_editor_sync_packet.py"
EDITOR_SYNC = ENGINE / "V17_CUSTOM_GPT_EDITOR_SYNC.md"


def test_canonical_wow_action_exposes_separate_ncaaf_spread_shadow_operation():
    spec = yaml.safe_load(SCHEMA.read_text())
    operation = spec["paths"]["/internal/v17/spread-forward-shadow"]["post"]
    assert operation["operationId"] == "scoreWowV17SpreadForwardShadow"
    assert operation["x-openai-isConsequential"] is False
    assert operation["security"] == [{"actionBearer": []}]
    schema_ref = operation["requestBody"]["content"]["application/json"]["schema"]["$ref"]
    assert schema_ref == "#/components/schemas/SpreadForwardShadowRequest"

    request = spec["components"]["schemas"]["SpreadForwardShadowRequest"]
    assert request["additionalProperties"] is False
    assert request["required"] == [
        "sport", "event_id", "event_start_time", "home_team", "away_team", "home_spread", "season"
    ]
    assert request["properties"]["sport"]["enum"] == ["NCAAF"]
    assert request["properties"]["home_spread"]["type"] == "number"


def test_existing_team_event_contract_remains_outright_winner_only():
    spec = yaml.safe_load(SCHEMA.read_text())
    request = spec["components"]["schemas"]["TeamEventRequest"]
    assert request["properties"]["market_family"]["enum"] == ["OUTRIGHT_WINNER"]


def test_editor_sync_packet_requires_spread_shadow_operation_without_claiming_live_editor_parity():
    builder = SYNC_BUILDER.read_text()
    docs = EDITOR_SYNC.read_text()
    assert '"scoreWowV17SpreadForwardShadow"' in builder
    assert "scoreWowV17SpreadForwardShadow" in docs
    assert "live editor" in docs.lower()
    assert "EXTERNAL_SYNC_REQUIRED" in docs
