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
    assert accepted.get_json()["run_id"] == "server-generated-run"
    assert start.call_args.kwargs["run_id"] is None
    assert start.call_args.kwargs["intended_date"] == "2026-08-20"
    assert start.call_args.kwargs["run_timezone"] == "America/New_York"

schema = client.get("/openapi.json").get_json()
operation = schema["paths"]["/wow/daily/run"]["post"]
body_schema = operation["requestBody"]["content"]["application/json"]["schema"]
assert operation["requestBody"]["required"] is True
assert {"date", "timezone", "idempotency_key"} <= set(body_schema["required"])
assert "run_id" not in body_schema["properties"]
for status_code in ("200", "202"):
    response_schema = operation["responses"][status_code]["content"]["application/json"]["schema"]
    response_ref = response_schema["$ref"]
    response_name = response_ref.rsplit("/", 1)[-1]
    dynamic_response = schema["components"]["schemas"][response_name]
    response_properties = dynamic_response["properties"]
    assert {
        "run_id", "run_date", "timezone", "run_status", "progress_stage",
        "deadline_at", "reused", "can_execute", "runtime_provenance",
    } <= set(response_properties)
    action_schema = yaml.safe_load(
        Path("gpt-action-schema-gate-engine.yaml").read_text()
    )
    action_response = action_schema["paths"]["/wow/daily/run"]["post"]["responses"][status_code]
    action_ref = action_response["content"]["application/json"]["schema"]["$ref"]
    static_response = action_schema["components"]["schemas"][action_ref.rsplit("/", 1)[-1]]
    assert dynamic_response["required"] == static_response["required"]
    assert set(response_properties) == set(static_response["properties"])
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