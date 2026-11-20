"""No-network acceptance tests for optional Highlightly source discovery."""
import json
from datetime import datetime, timezone

import httpx
import pytest

from v17.optional_highlightly_discovery import discover_matches, select_prefix, SPORT_PREFIXES

DATE = "2026-11-20"


def test_disabled_is_network_silent(monkeypatch):
    monkeypatch.delenv("WOW_HIGHLIGHTLY_SOURCE_ALLOWED", raising=False)
    receipt = discover_matches(prefix="nba", event_date=DATE, enabled=False)
    assert receipt["source_code"] == "SOURCE_DISABLED"
    assert receipt["request_count"] == 0
    assert receipt["model_feature_eligible"] is False
    assert receipt["prediction_authority"] is False
    assert receipt["can_execute"] is False


def test_bad_identity_date_and_rights_block_before_network(monkeypatch):
    monkeypatch.delenv("WOW_HIGHLIGHTLY_SOURCE_ALLOWED", raising=False)
    assert discover_matches(prefix="tennis", event_date=DATE, enabled=True,
                            key="secret")["source_code"] == "SOURCE_TARGET_INVALID"
    assert discover_matches(prefix="nba", event_date="2026-02-30", enabled=True,
                            key="secret")["source_code"] == "SOURCE_DATE_INVALID"
    assert discover_matches(prefix="nba", event_date=DATE, enabled=True,
                            key="secret")["source_code"] == "SOURCE_KEY_POLICY_NOT_ACCEPTED"


def test_absent_key_is_typed(monkeypatch):
    monkeypatch.setenv("WOW_HIGHLIGHTLY_SOURCE_ALLOWED", "true")
    monkeypatch.delenv("WOW_HIGHLIGHTLY_API_KEY", raising=False)
    result = discover_matches(prefix="nba", event_date=DATE, enabled=True)
    assert result["source_code"] == "SOURCE_CREDENTIAL_UNCONFIGURED"
    assert result["request_count"] == 0


def test_valid_response_is_research_only(monkeypatch):
    monkeypatch.setenv("WOW_HIGHLIGHTLY_SOURCE_ALLOWED", "true")
    observed = []

    def handler(request):
        observed.append(request)
        return httpx.Response(200, json={
            "data": [{"id": 123, "score": {"home": 99}},
                     {"id": 123}, {"id": "../../bad"}, {"foo": "missing"},
                     {"id": "M-124"}],
            "plan": {"tier": "BASIC", "message": "restricted"},
            "pagination": {"totalCount": 1200, "limit": 20, "offset": 0}
        }, headers={"x-ratelimit-requests-remaining": "97"})

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        result = discover_matches(prefix="nba", event_date=DATE, client=client,
                                  enabled=True, key="test-key")
    assert len(observed) == 1
    assert str(observed[0].url) == "https://sports.highlightly.net/nba/matches?date=2026-11-20&limit=20"
    assert observed[0].headers["x-rapidapi-key"] == "test-key"
    assert result["source_status"] == "OK"
    assert result["match_count"] == 2
    assert result["quota_remaining"] == 97
    assert result["plan_tier"] == "BASIC"
    assert result["official_event_identity_verified"] is False
    assert result["model_rescore_verified"] is False
    assert "score" not in json.dumps(result)
    assert "test-key" not in json.dumps(result)


@pytest.mark.parametrize("status,expected", [
    (429, "SOURCE_RATE_LIMITED"), (401, "SOURCE_AUTH_REJECTED"),
    (503, "SOURCE_UPSTREAM_UNAVAILABLE"),
    (302, "SOURCE_REDIRECT_NOT_ALLOWED"),
])
def test_errors_report_native_source_failure(monkeypatch, status, expected):
    monkeypatch.setenv("WOW_HIGHLIGHTLY_SOURCE_ALLOWED", "true")
    def handler(request):
        return httpx.Response(status, headers={"Location": "https://example.com/"})
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        out = discover_matches(prefix="american-football", event_date=DATE,
                               client=client, enabled=True, key="unit-key")
    assert out["source_code"] == expected
    assert out["match_count"] == 0
    assert out["model_feature_eligible"] is False


def test_invalid_json_and_empty_provider_data(monkeypatch):
    monkeypatch.setenv("WOW_HIGHLIGHTLY_SOURCE_ALLOWED", "true")
    for response, code in [
        (httpx.Response(200, content=b"{not json"), "SOURCE_INVALID_JSON"),
        (httpx.Response(200, json={"records": []}), "SOURCE_OUTPUT_INVALID"),
        (httpx.Response(200, json={"data": []}), "SOURCE_NO_VALID_MATCHES"),
    ]:
        with httpx.Client(transport=httpx.MockTransport(lambda request: response)) as client:
            result = discover_matches(prefix="cricket", event_date=DATE,
                                      client=client, enabled=True, key="x")
        assert result["source_code"] == code


def test_rotation_is_deterministic_and_aware():
    now = datetime(2026, 11, 20, 12, 0, tzinfo=timezone.utc)
    assert select_prefix(now) in SPORT_PREFIXES
    assert select_prefix(now) == select_prefix(now)
    with pytest.raises(ValueError):
        select_prefix(now.replace(tzinfo=None))


def test_existing_scout_workflow_is_opt_in_with_a_receipt():
    from pathlib import Path
    workflow = (Path(__file__).resolve().parents[2] /
                ".github/workflows/wow-v17-scout-source-refresh.yml").read_text()
    assert "WOW_HIGHLIGHTLY_ENABLED: ${{ vars.WOW_HIGHLIGHTLY_ENABLED || 'false' }}" in workflow
    assert "WOW_HIGHLIGHTLY_SOURCE_ALLOWED: ${{ vars.WOW_HIGHLIGHTLY_SOURCE_ALLOWED || 'false' }}" in workflow
    assert "secrets.WOW_HIGHLIGHTLY_API_KEY" in workflow
    assert "python v17/optional_highlightly_discovery.py" in workflow
    assert "Upload optional Highlightly discovery receipt" in workflow
    assert "continue-on-error: true" in workflow
