"""Isolated HTTP regressions for canonical WOW Daily requests.

The app bootstraps manifest state when imported. Keep these real Flask-client
checks in a child interpreter so lifecycle-only tests retain their isolated
module state in the parent pytest process.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path


_ROOT = Path(__file__).parents[1]


def test_daily_http_contract_and_dynamic_openapi_in_isolated_process():
    script = r'''
import os
from unittest.mock import patch
from pathlib import Path
import yaml

os.environ["SCORING_API_KEY"] = "daily-contract-key"
import app as app_module

app_module.app.config["TESTING"] = True
client = app_module.app.test_client()
valid_body = {
    "date": "2026-08-20",
    "timezone": "America/New_York",
    "idempotency_key": "daily-contract-test",
}

assert client.post("/wow/daily/run", json=valid_body).status_code == 401

with (
    patch(
        "gate_engine.runtime_provenance.build_route_provenance",
        return_value={"can_execute": False},
    ),
    patch(
        "gate_engine.runtime_provenance.provenance_blocker",
        return_value="RUNTIME_PROVENANCE:BACKEND_NOT_VERIFIED:TEST",
    ),
    patch("gate_engine.daily_run_lifecycle.start_run") as start,
):
    rejected = client.post(
        "/wow/daily/run",
        json=valid_body,
        headers={"X-API-Key": "daily-contract-key"},
    )
    assert rejected.status_code == 409
    assert rejected.get_json()["error"] == "RUN_INVALID_RUNTIME_PROVENANCE"
    assert rejected.get_json()["can_execute"] is False
    assert start.call_count == 0

with patch("gate_engine.daily_run_lifecycle.start_run") as start:
    missing = client.post(
        "/wow/daily/run",
        json={"idempotency_key": "missing-date-and-timezone"},
        headers={"X-API-Key": "daily-contract-key"},
    )
    assert missing.status_code == 400
    assert start.call_count == 0

    supplied_id = client.post(
        "/wow/daily/run",
        json={**valid_body, "run_id": "caller-id"},
        headers={"X-API-Key": "daily-contract-key"},
    )
    assert supplied_id.status_code == 400
    assert supplied_id.get_json()["error"] == "RUN_ID_SERVER_GENERATED"

for response_field in (
    "latest_detail",
    "rows_committed",
    "progress_detail",
    "total_discovered",
):
    with patch("gate_engine.daily_run_lifecycle.start_run") as start:
        response_field_request = client.post(
            "/wow/daily/run",
            json={**valid_body, response_field: "caller-controlled"},
            headers={"X-API-Key": "daily-contract-key"},
        )
    assert response_field_request.status_code == 400
    assert response_field_request.get_json()["error"] == (
        "DAILY_RESPONSE_FIELDS_SERVER_OWNED"
    )
    assert start.call_count == 0

with (
    patch(
        "gate_engine.runtime_provenance.build_route_provenance",
        return_value={"can_execute": False},
    ),
    patch(
        "gate_engine.runtime_provenance.provenance_blocker",
        return_value=None,
    ),
    patch(
        "gate_engine.daily_run_lifecycle.start_run",
        return_value={
            "ok": True,
            "run_id": "server-generated-run",
            "run_date": "2026-08-20",
            "timezone": "America/New_York",
            "run_status": "IN_PROGRESS",
            "progress_stage": "STARTING",
            "progress_detail": "Detached runner claimed run",
            "total_discovered": None,
            "can_execute": False,
        },
    ) as start,
):
    accepted = client.post(
        "/wow/daily/run",
        json=valid_body,
        headers={"X-API-Key": "daily-contract-key"},
    )
    assert accepted.status_code == 202
    acknowledgement = accepted.get_json()
    assert acknowledgement["run_id"] == "server-generated-run"
    assert acknowledgement["latest_detail"] == acknowledgement["progress_detail"]
    assert acknowledgement["rows_committed"] == acknowledgement["total_discovered"]
    assert start.call_args.kwargs["run_id"] is None
    assert start.call_args.kwargs["intended_date"] == "2026-08-20"
    assert start.call_args.kwargs["run_timezone"] == "America/New_York"

with (
    patch(
        "storage.daily_manifest.get_run",
        return_value={
            "run_id": "server-generated-run",
            "run_status": "IN_PROGRESS",
            "progress_stage": "SCORING",
            "progress_detail": "Evaluating canonical board",
            "total_discovered": 8,
            "request_scope": "MONEYLINE_REMAINING_TODAY",
        },
    ),
    patch("storage.daily_manifest.get_run_rows", return_value=[]),
):
    manifest = client.get(
        "/wow/daily/manifest/server-generated-run",
        headers={"X-API-Key": "daily-contract-key"},
    )
    assert manifest.status_code == 200
    body = manifest.get_json()
    assert body["terminal"] is False
    assert body["row_count"] == 0
    assert body["total_discovered"] == 8
    assert body["latest_detail"] == body["progress_detail"]
    assert body["rows_committed"] == body["total_discovered"]
    assert body["run"]["latest_detail"] == body["run"]["progress_detail"]
    assert body["run"]["rows_committed"] == body["run"]["total_discovered"]
    assert body["progress_stage"] == "SCORING"
    assert body["progress_detail"] == "Evaluating canonical board"
    assert body["scope"] == "MONEYLINE_REMAINING_TODAY"

with patch(
    "storage.daily_manifest.list_runs",
    return_value=[{
        "run_id": "server-generated-run",
        "run_status": "IN_PROGRESS",
        "progress_stage": "SCORING",
        "progress_detail": "Evaluating canonical board",
        "total_discovered": 8,
    }],
):
    runs_response = client.get(
        "/wow/daily/runs",
        headers={"X-API-Key": "daily-contract-key"},
    )
    assert runs_response.status_code == 200
    record = runs_response.get_json()["runs"][0]
    assert record["latest_detail"] == record["progress_detail"]
    assert record["rows_committed"] == record["total_discovered"]

schema = client.get("/openapi.json").get_json()
operation = schema["paths"]["/wow/daily/run"]["post"]
body_schema = operation["requestBody"]["content"]["application/json"]["schema"]
assert operation["requestBody"]["required"] is True
assert {"date", "timezone", "idempotency_key"} <= set(body_schema["required"])
assert "run_id" not in body_schema["properties"]
assert not {
    "progress_stage", "progress_detail", "row_count", "total_discovered",
    "latest_detail", "rows_committed",
} & set(body_schema["properties"])
for status_code in ("200", "202"):
    response_schema = operation["responses"][status_code]["content"]["application/json"]["schema"]
    response_ref = response_schema["$ref"]
    response_name = response_ref.rsplit("/", 1)[-1]
    dynamic_response = schema["components"]["schemas"][response_name]
    response_properties = dynamic_response["properties"]
    assert {
        "run_id", "run_date", "timezone", "run_status", "progress_stage",
        "progress_detail", "total_discovered", "latest_detail",
        "rows_committed", "deadline_at", "reused", "can_execute",
        "runtime_provenance",
    } <= set(response_properties)
    action_schema = yaml.safe_load(
        Path("gpt-action-schema-gate-engine.yaml").read_text()
    )
    action_response = action_schema["paths"]["/wow/daily/run"]["post"]["responses"][status_code]
    action_ref = action_response["content"]["application/json"]["schema"]["$ref"]
    static_response = action_schema["components"]["schemas"][action_ref.rsplit("/", 1)[-1]]
    assert dynamic_response["required"] == static_response["required"]
    assert set(response_properties) == set(static_response["properties"])

manifest_operation = schema["paths"]["/wow/daily/manifest/{run_id}"]["get"]
runs_operation = schema["paths"]["/wow/daily/runs"]["get"]
assert manifest_operation["operationId"] == "getWowDailyManifest"
assert runs_operation["operationId"] == "listWowDailyRuns"
manifest_schema = schema["components"]["schemas"]["DailyRunManifest"]
assert {
    "run_status", "terminal", "progress_stage", "progress_detail",
    "row_count", "total_discovered", "latest_detail", "rows_committed",
    "scope",
} <= set(manifest_schema["properties"])
assert manifest_schema["properties"]["latest_detail"]["deprecated"] is True
assert manifest_schema["properties"]["rows_committed"]["deprecated"] is True
run_list_schema = schema["paths"]["/wow/daily/runs"]["get"]["responses"]["200"]
run_items = run_list_schema["content"]["application/json"]["schema"]
assert run_items["properties"]["runs"]["items"]["$ref"].endswith("/DailyRunRecord")
dynamic_record = schema["components"]["schemas"]["DailyRunRecord"]["properties"]
assert {"progress_stage", "progress_detail", "total_discovered",
        "latest_detail", "rows_committed"} <= set(dynamic_record)
'''
    completed = subprocess.run(
        [sys.executable, "-c", script],
        cwd=_ROOT,
        env=os.environ.copy(),
        text=True,
        capture_output=True,
        timeout=90,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr