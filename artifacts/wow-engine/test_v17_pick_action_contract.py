from pathlib import Path

import yaml
from pydantic import ValidationError

from pick_request_runtime import PickRequestBatch, PickRequestRow


V17_ACTION = Path(__file__).parent / "v17" / "openapi.wow-betting-engine.v17.yaml"


def test_v17_pick_request_openapi_matches_backend_pydantic_contract() -> None:
    action = yaml.safe_load(V17_ACTION.read_text())
    schema = action["components"]["schemas"]["PickRequestRow"]
    backend_schema = PickRequestRow.model_json_schema()

    assert schema["additionalProperties"] is False
    assert set(schema["properties"]) == set(backend_schema["properties"])
    assert set(schema["required"]) == set(backend_schema["required"])

    source_type = schema["properties"]["source_type"]
    assert set(source_type["enum"]) == {
        "SCREENSHOT",
        "PDF",
        "AUTONOMOUS_DISCOVERY",
        "PASTED_BOARD",
        "NORMALIZED",
    }
    assert source_type["default"] == "NORMALIZED"


def test_v17_pick_request_batch_rejects_unknown_row_fields() -> None:
    payload = {
        "request_id": "contract-regression",
        "rows": [
            {
                "event_id": "event-1",
                "event_start_time": "2099-01-01T00:00:00Z",
                "sport": "MLB",
                "player": "Example Pitcher",
                "stat_type": "PITCHER_STRIKEOUTS",
                "line": 5.5,
                "direction": "MORE",
                "unknown_field": "must-fail",
            }
        ],
    }

    try:
        PickRequestBatch.model_validate(payload)
    except ValidationError as exc:
        assert "unknown_field" in str(exc)
    else:
        raise AssertionError("backend unexpectedly accepted an unknown row field")
