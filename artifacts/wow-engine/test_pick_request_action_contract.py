from pathlib import Path

import yaml
from openapi_spec_validator import validate_spec


def _schema():
    path = Path(__file__).with_name("openapi.pick-request-action.yaml")
    return yaml.safe_load(path.read_text())


def test_pick_request_action_exposes_only_governed_batch_boundary():
    schema = _schema()
    validate_spec(schema)
    paths = schema["paths"]
    assert list(paths) == ["/score-pick-request"]
    operation = paths["/score-pick-request"]["post"]
    assert operation["operationId"] == "scoreWowPickRequest"
    assert operation["security"] == [{"actionBearer": []}]


def test_pick_request_action_requires_candidate_identity_but_not_caller_hydration():
    schema = _schema()
    row = schema["components"]["schemas"]["PickRequestRow"]
    required = set(row["required"])
    assert {
        "event_id",
        "event_start_time",
        "sport",
        "player",
        "stat_type",
        "line",
        "direction",
    } <= required
    assert "evidence" not in required
    assert "evidence" in row["properties"]
    assert set(row["properties"]["source_type"]["enum"]) == {
        "SCREENSHOT",
        "PDF",
        "AUTONOMOUS_DISCOVERY",
        "PASTED_BOARD",
        "NORMALIZED",
    }

    forbidden_caller_fields = {
        "probability",
        "calibrated_probability",
        "calibrated_probability_lower_bound",
        "edge",
        "model_artifact_version",
        "model_family",
        "approval_label",
        "terminal_label",
    }
    assert forbidden_caller_fields.isdisjoint(row["properties"])
    assert row["additionalProperties"] is False


def test_supplied_raw_evidence_still_requires_l10_and_response_preserves_no_execution():
    schema = _schema()
    evidence = schema["components"]["schemas"]["RawPropEvidence"]
    assert evidence["properties"]["game_log"]["minItems"] == 10
    assert evidence["properties"]["box_score_log"]["minItems"] == 10

    response = schema["paths"]["/score-pick-request"]["post"]["responses"]["200"]["content"]["application/json"]["schema"]
    assert response["properties"]["can_execute"]["const"] is False
    assert response["properties"]["run_controller_status"]["enum"] == [
        "COMPLETE",
        "DEGRADED",
        "BLOCKED",
    ]
    assert {
        "rows_in",
        "rows_completed",
        "rows_held",
        "rows_rejected",
        "reconciliation_pass",
    } <= set(response["required"])


def test_action_description_preserves_fail_closed_model_ownership():
    schema = _schema()
    description = schema["paths"]["/score-pick-request"]["post"]["description"]
    assert "Missing evidence never authorizes a qualitative or L5/L10 fallback" in description
    assert "Probabilities, model artifacts, calibration outputs, edge, and approval labels are always backend-owned" in description
    assert "can_execute=false" in description
