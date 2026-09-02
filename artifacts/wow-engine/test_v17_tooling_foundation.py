from __future__ import annotations

import importlib

import pandera
import pytest

from v17_data_contracts import validate_external_evidence
from v17_observability import initialize_observability


def _valid_row() -> dict:
    return {
        "source_id": "NWS-KDFW-20260902T100000Z",
        "source_kind": "OFFICIAL_WEATHER",
        "captured_at_utc": "2026-09-02T10:00:00Z",
        "source_published_at_utc": "2026-09-02T09:55:00Z",
        "schema_fingerprint": "nws-grid-v1",
        "payload_sha256": "a" * 64,
        "completeness_score": 0.98,
        "can_execute": False,
    }


def test_external_evidence_contract_accepts_typed_nonexecuting_row():
    validated = validate_external_evidence([_valid_row()])
    assert validated.iloc[0]["can_execute"] == False  # noqa: E712
    assert validated.iloc[0]["completeness_score"] == pytest.approx(0.98)


def test_external_evidence_contract_rejects_execution_authority():
    row = _valid_row()
    row["can_execute"] = True
    with pytest.raises(pandera.errors.SchemaError):
        validate_external_evidence([row])


def test_external_evidence_contract_rejects_invalid_payload_hash():
    row = _valid_row()
    row["payload_sha256"] = "not-a-hash"
    with pytest.raises(pandera.errors.SchemaError):
        validate_external_evidence([row])


def test_external_evidence_contract_rejects_timezone_naive_capture():
    row = _valid_row()
    row["captured_at_utc"] = "2026-09-02T10:00:00"
    with pytest.raises(ValueError, match="TIMEZONE_REQUIRED:captured_at_utc"):
        validate_external_evidence([row])


def test_observability_is_inert_without_dsn(monkeypatch):
    monkeypatch.delenv("SENTRY_DSN", raising=False)
    assert initialize_observability() == {
        "status": "DISABLED_NOT_CONFIGURED",
        "provider": "SENTRY",
        "can_execute": False,
    }


def test_production_dependencies_are_importable():
    assert importlib.import_module("sentry_sdk")
    assert importlib.import_module("pandera")
