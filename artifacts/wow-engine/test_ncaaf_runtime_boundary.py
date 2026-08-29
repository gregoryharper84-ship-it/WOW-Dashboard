from pathlib import Path

import pytest
from fastapi import HTTPException

import api_ncaaf_acceptance as api


def test_readiness_reports_external_and_model_blockers(monkeypatch):
    monkeypatch.delenv("CFBD_API_KEY", raising=False)
    monkeypatch.setattr(api, "_safe_count", lambda table: 0)
    monkeypatch.setattr(api, "_artifact_state", lambda: {"ok": False, "code": "NCAAF_CERTIFIED_MODEL_ARTIFACT_NOT_FOUND"})
    monkeypatch.setattr(api, "_calibrator_state", lambda version: {"ok": False, "code": "NCAAF_MODEL_ARTIFACT_UNAVAILABLE"})

    result = api.ncaaf_readiness()
    assert result["ncaaf_controlling_model"] == "MODEL_UNAVAILABLE"
    assert result["ncaaf_trust_state"] == "NCAAF_TEST_ONLY"
    assert result["probability_publishable"] is False
    assert result["can_execute"] is False
    assert "CFBD_API_KEY_MISSING" in result["blockers"]
    assert "NCAAF_CERTIFIED_MODEL_ARTIFACT_NOT_FOUND" in result["blockers"]


def test_hydration_missing_key_fails_closed(monkeypatch):
    monkeypatch.delenv("CFBD_API_KEY", raising=False)
    with pytest.raises(HTTPException) as exc:
        api.hydrate_ncaaf_history(2025, 1, 1)
    assert exc.value.status_code == 503
    assert exc.value.detail["code"] == "CFBD_API_KEY_MISSING"
    assert exc.value.detail["probability_publishable"] is False
    assert exc.value.detail["can_execute"] is False


def test_runtime_contract_is_service_role_only_and_non_executable():
    sql = Path("ncaaf_runtime_contract.sql").read_text()
    assert "enable row level security" in sql.lower()
    assert "revoke all on table public.wow_ncaaf_event_feature_snapshots" in sql.lower()
    assert "grant all on table public.wow_ncaaf_event_feature_snapshots" in sql.lower()
    assert "to service_role" in sql.lower()
    assert "security invoker" in sql.lower()
    assert "can_execute = false" in sql.lower()
    assert "feature_as_of < event_start_time" in sql
    assert "injury_evidence_timestamp < event_start_time" in sql


def test_readiness_and_hydration_routes_are_authenticated():
    source = Path("api_ncaaf_acceptance.py").read_text()
    assert '"/internal/ncaaf/readiness"' in source
    assert '"/internal/ncaaf/hydrate-history"' in source
    assert "dependencies=[_auth]" in source
    assert "probability_publishable\": False" in source
    assert "can_execute\": False" in source
