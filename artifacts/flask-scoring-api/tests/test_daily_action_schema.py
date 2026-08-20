"""Contract regression tests for the canonical WOW Daily GPT Action."""

from pathlib import Path

import yaml


SCHEMA_PATH = Path(__file__).parents[1] / "gpt-action-schema-gate-engine.yaml"


def _daily_operation():
    document = yaml.safe_load(SCHEMA_PATH.read_text())
    return document, document["paths"]["/wow/daily/run"]["post"]


def _resolve_schema(document, schema):
    ref = schema.get("$ref")
    if not ref:
        return schema
    assert ref.startswith("#/components/schemas/")
    return document["components"]["schemas"][ref.rsplit("/", 1)[-1]]


def test_daily_action_requires_canonical_request_identity():
    _document, operation = _daily_operation()
    body_schema = operation["requestBody"]["content"]["application/json"]["schema"]

    assert operation["requestBody"]["required"] is True
    assert {"date", "timezone", "idempotency_key"} <= set(body_schema["required"])
    assert body_schema["properties"]["date"]["format"] == "date"
    assert body_schema["properties"]["timezone"]["type"] == "string"
    assert "idempotency_key" in body_schema["required"]
    field = body_schema["properties"]["idempotency_key"]
    assert field["type"] == "string"
    assert field["minLength"] == 1
    assert field["maxLength"] == 255


def test_daily_action_keeps_run_id_server_generated():
    document, operation = _daily_operation()
    body_schema = operation["requestBody"]["content"]["application/json"]["schema"]
    response_schema = _resolve_schema(
        document,
        operation["responses"]["200"]["content"]["application/json"]["schema"],
    )

    assert "run_id" not in body_schema["properties"]
    assert "run_id" in response_schema["properties"]


def test_daily_action_documents_acknowledgement_for_both_success_statuses():
    document, operation = _daily_operation()
    for status_code in ("200", "202"):
        response_schema = _resolve_schema(
            document,
            operation["responses"][status_code]["content"]["application/json"]["schema"],
        )
        assert {
            "run_id", "run_date", "timezone", "run_status", "progress_stage",
            "deadline_at", "reused", "can_execute", "runtime_provenance",
        } <= set(response_schema["properties"])