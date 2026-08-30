from pathlib import Path

import yaml
from openapi_spec_validator import validate_spec


def test_pick_request_action_is_exposed_and_authenticated():
    action = yaml.safe_load(Path("openapi.custom-gpt.template.yaml").read_text())
    validate_spec(action)

    operation = action["paths"]["/pick-request/props"]["post"]
    assert operation["operationId"] == "scoreWowPickRequestProps"
    assert operation["security"] == [{"actionBearer": []}]
    assert operation["requestBody"]["content"]["application/json"]["schema"] == {
        "$ref": "#/components/schemas/PickRequestBatch"
    }

    candidate = action["components"]["schemas"]["PickRequestPropCandidate"]
    assert "source_snapshot_id" not in candidate["required"]
    assert {
        "event_id",
        "event_start_time",
        "sport",
        "player",
        "stat_type",
        "line",
        "direction",
    }.issubset(set(candidate["required"]))

    row = action["components"]["schemas"]["PickRequestRow"]
    assert set(row["properties"]["source_type"]["enum"]) == {
        "SCREENSHOT",
        "PDF",
        "AUTONOMOUS_DISCOVERY",
        "PASTED_BOARD",
        "NORMALIZED",
    }


def test_action_contract_remains_analytical_only():
    action = yaml.safe_load(Path("openapi.custom-gpt.template.yaml").read_text())
    description = action["paths"]["/pick-request/props"]["post"]["description"]
    assert "never places or authorizes a wager" in description
    assert "can_execute=false" in description
