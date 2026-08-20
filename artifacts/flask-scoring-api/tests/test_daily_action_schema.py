"""Contract regression tests for the canonical WOW Daily GPT Action."""

from pathlib import Path

import yaml


SCHEMA_PATH = Path(__file__).parents[1] / "gpt-action-schema-gate-engine.yaml"


def _daily_operation():
    document = yaml.safe_load(SCHEMA_PATH.read_text())
    return document["paths"]["/wow/daily/run"]["post"]


def test_daily_action_exposes_required_idempotency_key():
    operation = _daily_operation()
    body_schema = operation["requestBody"]["content"]["application/json"]["schema"]

    assert "idempotency_key" in body_schema["required"]
    field = body_schema["properties"]["idempotency_key"]
    assert field["type"] == "string"
    assert field["minLength"] == 1
    assert field["maxLength"] == 255


def test_daily_action_keeps_run_id_server_generated():
    operation = _daily_operation()
    body_schema = operation["requestBody"]["content"]["application/json"]["schema"]

    assert "run_id" not in body_schema["properties"]
    assert "run_id" in operation["responses"]["200"]["content"]["application/json"]["schema"]["properties"]