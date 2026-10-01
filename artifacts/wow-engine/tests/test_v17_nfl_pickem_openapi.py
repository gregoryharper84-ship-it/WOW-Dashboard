from __future__ import annotations

from pathlib import Path

import yaml


def test_wow_action_schema_exposes_durable_governed_nfl_pickem_board():
    schema_path = Path(__file__).parents[1] / "v17" / "openapi.wow-betting-engine.v17.yaml"
    spec = yaml.safe_load(schema_path.read_text())

    submit = spec["paths"]["/v17/nfl-pickem-submit"]["post"]
    assert submit["operationId"] == "submitWowV17NFLPickemBoard"
    assert submit["x-openai-isConsequential"] is False
    assert submit["security"] == [{"actionBearer": []}]
    request_schema = submit["requestBody"]["content"]["application/json"]["schema"]
    assert request_schema == {"$ref": "#/components/schemas/AsyncNFLPickemSubmitRequest"}
    assert "202" in submit["responses"]

    poll = spec["paths"]["/v17/nfl-pickem-run/{run_id}"]["get"]
    assert poll["operationId"] == "getWowV17NFLPickemRun"
    assert poll["x-openai-isConsequential"] is False
    assert poll["security"] == [{"actionBearer": []}]
    run_id = next(param for param in poll["parameters"] if param["name"] == "run_id")
    assert run_id["in"] == "path"
    assert run_id["required"] is True

    request = spec["components"]["schemas"]["AsyncNFLPickemSubmitRequest"]
    assert request["additionalProperties"] is False
    assert request["required"] == ["requested_slate_dates"]
    assert request["properties"]["strategy_mode"]["enum"] == ["MAX_EXPECTED_CORRECT"]
    assert request["properties"]["expected_game_count"]["default"] == 16
    assert request["properties"]["idempotency_key"]["minLength"] == 8


def test_synchronous_pickem_route_is_not_exposed_to_live_custom_gpt_action():
    schema_path = Path(__file__).parents[1] / "v17" / "openapi.wow-betting-engine.v17.yaml"
    spec = yaml.safe_load(schema_path.read_text())
    assert "/v17/nfl-pickem-board" not in spec["paths"]
