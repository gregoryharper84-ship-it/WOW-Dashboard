from pathlib import Path

import yaml


def _engine_root() -> Path:
    return Path(__file__).resolve().parents[1]


def test_primary_action_schema_exposes_run_control_recovery_operations():
    root = _engine_root()
    primary = yaml.safe_load((root / "v17" / "openapi.wow-betting-engine.v17.yaml").read_text())
    companion = yaml.safe_load((root / "v17" / "openapi.wow-betting-engine.v17.run-control.yaml").read_text())
    paths = primary["paths"]

    assert paths["/v17/pick-request-runs/{request_id}"]["get"]["operationId"] == "getWowV17PickRequestRunState"
    assert paths["/v17/pick-request-runs/resumable"]["post"]["operationId"] == "runWowV17ResumablePickRequest"
    assert paths["/v17/pick-request-runs/{request_id}/close"]["post"]["operationId"] == "closeWowV17PickRequestRun"
    assert primary["components"]["schemas"]["ResumablePickRunRequest"]["properties"]["rows"]["maxItems"] == 1000

    # The standalone companion remains a repository/reference contract, but the
    # live GPT installs only the single canonical schema because the editor
    # rejects duplicate Action domains.
    assert primary["servers"][0]["url"] == companion["servers"][0]["url"]
    for path in (
        "/v17/pick-request-runs/{request_id}",
        "/v17/pick-request-runs/resumable",
        "/v17/pick-request-runs/{request_id}/close",
    ):
        assert path in companion["paths"]


def test_live_instructions_require_server_side_large_board_resume_and_fit_editor_limit():
    root = _engine_root()
    text = (root / "WOW_V17_CUSTOM_GPT_INSTRUCTIONS.txt").read_text()
    assert len(text) <= 8000
    assert len(text.encode("utf-8")) <= 7500
    assert "runWowV17ResumablePickRequest" in text
    assert "getWowV17PickRequestRunState" in text
    assert "closeWowV17PickRequestRun" in text
    assert "Run-control operations are merged into the canonical Action schema" in text
    assert "companion file is repository reference only" in text
    assert "can_execute=false" in text


def test_run_control_migration_adds_payload_and_closure_fields():
    root = _engine_root()
    sql = (root / "migrations" / "20260921_pick_request_run_control.sql").read_text().lower()
    assert "add column if not exists request_payload jsonb" in sql
    assert "add column if not exists identity_version" in sql
    assert "add column if not exists closure_code" in sql
    assert "add column if not exists closed_at" in sql
    assert "reopen_allowed" in sql
