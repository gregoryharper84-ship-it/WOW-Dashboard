from __future__ import annotations

from pathlib import Path

import yaml


def test_wow_action_schema_exposes_governed_nfl_pickem_board():
    schema_path = Path(__file__).parents[1] / "v17" / "openapi.wow-betting-engine.v17.yaml"
    spec = yaml.safe_load(schema_path.read_text())

    operation = spec["paths"]["/v17/nfl-pickem-board"]["post"]
    assert operation["operationId"] == "runWowV17NFLPickemBoard"
    assert operation["x-openai-isConsequential"] is False
    assert operation["security"] == [{"actionBearer": []}]
    request_schema = operation["requestBody"]["content"]["application/json"]["schema"]
    assert request_schema == {"$ref": "#/components/schemas/NFLPickemBoardRequest"}

    request = spec["components"]["schemas"]["NFLPickemBoardRequest"]
    assert request["additionalProperties"] is False
    assert request["required"] == ["requested_slate_dates"]
    assert request["properties"]["strategy_mode"]["enum"] == ["MAX_EXPECTED_CORRECT"]
    assert request["properties"]["expected_game_count"]["default"] == 16
