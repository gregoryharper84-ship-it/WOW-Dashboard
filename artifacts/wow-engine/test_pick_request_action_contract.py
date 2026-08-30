from pathlib import Path

import yaml


def _schema():
    path = Path(__file__).with_name("openapi.pick-request-action.yaml")
    return yaml.safe_load(path.read_text())


def test_pick_request_action_exposes_only_governed_batch_boundary():
    schema = _schema()
    paths = schema["paths"]
    assert list(paths) == ["/score-pick-request"]
    operation = paths["/score-pick-request"]["post"]
    assert operation["operationId"] == "scoreWowPickRequest"
    assert operation["security"] == [{"actionBearer": []}]


def test_pick_request_action_requires_hydrated_evidence_not_probability_inputs():
    schema = _schema()
    row = schema["components"]["schemas"]["PickRequestRow"]
    required = set(row["required"])
    assert {"event_id", "event_start_time", "sport", "player", "stat_type", "line", "direction", "evidence"} <= required

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


def test_pick_request_action_requires_l10_and_preserves_no_execution():
    schema = _schema()
    evidence = schema["components"]["schemas"]["RawPropEvidence"]
    assert evidence["properties"]["game_log"]["minItems"] == 10
    assert evidence["properties"]["box_score_log"]["minItems"] == 10
    response = schema["paths"]["/score-pick-request"]["post"]["responses"]["200"]["content"]["application/json"]["schema"]
    assert response["properties"]["can_execute"]["const"] is False
    assert {"rows_in", "rows_completed", "rows_held", "rows_rejected", "reconciliation_pass"} <= set(response["required"])
