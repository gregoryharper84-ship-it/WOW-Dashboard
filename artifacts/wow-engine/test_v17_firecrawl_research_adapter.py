"""Offline regressions for optional Firecrawl research ingestion (no live API calls)."""
import json

import httpx
import pytest

from v17 import scout_source_refresh as scout
from v17.firecrawl_research_adapter import (
    MAX_MARKDOWN_CHARS, MAX_TARGETS_PER_REFRESH, fetch_firecrawl_page,
)
from v17.research_source_adapters import ResearchFetchResult
from v17.research_source_snapshot_store import snapshot_id_for

SPORT = "basketball_nba"
CAPABILITY = "injuries"
URL = "https://www.nba.com/news"


def test_disabled_by_default_never_calls_optional_source(monkeypatch):
    monkeypatch.delenv("WOW_FIRECRAWL_ENABLED", raising=False)
    monkeypatch.setattr(scout, "SPORTSDATAIO_CAPABILITIES", ())
    monkeypatch.setattr(scout, "fetch_firecrawl_page", lambda *args: pytest.fail("optional source invoked"))
    response = scout.refresh(sports=(SPORT,))
    assert response["status"] == "COMPLETE"
    assert "optional_firecrawl" not in response
    assert response["prediction_authority"] is False
    assert response["can_execute"] is False


def test_allowlisted_page_retains_url_content_hash_and_research_only_authority(monkeypatch):
    monkeypatch.setenv("FIRECRAWL_API_KEY", "private-example-key")
    observed = {}

    def handler(request):
        observed["body"] = json.loads(request.content)
        observed["authorization"] = request.headers["authorization"]
        return httpx.Response(200, json={
            "success": True,
            "data": {
                "markdown": "# Injury update",
                "metadata": {
                    "title": "NBA report",
                    "sourceURL": "https://www.nba.com/news",
                    "publishedTime": "2026-10-28T10:00:00Z",
                },
            },
        })

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        response = fetch_firecrawl_page(SPORT, CAPABILITY, URL, client=client, allowed_domains=("nba.com",))
    assert response.ok and response.code == "SOURCE_FETCH_OK"
    assert observed["body"] == {"url": URL, "formats": ["markdown"], "onlyMainContent": True}
    assert observed["authorization"] == "Bearer private-example-key"
    assert response.data["source_url"] == URL
    assert response.data["evidence_only"] is True
    assert len(response.data["content_sha256"]) == 64
    assert response.data["page_claimed_published_at"] == "2026-10-28T10:00:00Z"
    assert response.prediction_authority is False
    assert response.can_execute is False
    assert "private-example-key" not in str(response.to_dict())


@pytest.mark.parametrize("url", [
    "http://www.nba.com/news",
    "https://nba.com.evil.example/news",
    "https://localhost/news",
    "https://127.0.0.1/news",
    "https://www.nba.com:8888/news",
    "https://user:password@www.nba.com/news",
    "https://www.nba.com/news?api_key=hidden",
    "https://www.nba.com@evil.example/news",
])
def test_unsafe_or_non_allowlisted_targets_block_before_network(monkeypatch, url):
    monkeypatch.setenv("FIRECRAWL_API_KEY", "test")
    with httpx.Client(transport=httpx.MockTransport(
        lambda request: pytest.fail("unsafe URL was fetched")
    )) as client:
        response = fetch_firecrawl_page(SPORT, CAPABILITY, url, client=client, allowed_domains=("nba.com",))
    assert response.ok is False
    assert response.code == "SOURCE_TARGET_NOT_ALLOWLISTED"


def test_credential_missing_is_source_only_failure(monkeypatch):
    monkeypatch.delenv("FIRECRAWL_API_KEY", raising=False)
    monkeypatch.delenv("WOW_FIRECRAWL_API_KEY", raising=False)
    response = fetch_firecrawl_page(SPORT, CAPABILITY, URL, allowed_domains=("nba.com",))
    assert response.code == "SOURCE_CREDENTIAL_UNCONFIGURED"
    assert response.prediction_authority is False
    assert response.can_execute is False


@pytest.mark.parametrize("status,expected", [
    (401, "SOURCE_AUTH_FAILED"),
    (429, "SOURCE_RATE_LIMITED"),
    (503, "SOURCE_UPSTREAM_UNAVAILABLE"),
])
def test_http_failures_do_not_become_model_failures(monkeypatch, status, expected):
    monkeypatch.setenv("FIRECRAWL_API_KEY", "test")
    with httpx.Client(transport=httpx.MockTransport(
        lambda request: httpx.Response(status)
    )) as client:
        response = fetch_firecrawl_page(SPORT, CAPABILITY, URL, client=client, allowed_domains=("nba.com",))
    assert response.code == expected
    assert response.status == status


def test_redirect_outside_allowlist_blocked(monkeypatch):
    monkeypatch.setenv("FIRECRAWL_API_KEY", "test")
    with httpx.Client(transport=httpx.MockTransport(
        lambda request: httpx.Response(200, json={
            "success": True, "data": {
                "markdown": "Some content",
                "metadata": {"sourceURL": "https://evil.example/post"},
            },
        })
    )) as client:
        response = fetch_firecrawl_page(SPORT, CAPABILITY, URL, client=client, allowed_domains=("nba.com",))
    assert response.code == "SOURCE_REDIRECT_NOT_ALLOWLISTED"


def test_content_is_bounded_and_full_content_hash_retained(monkeypatch):
    monkeypatch.setenv("FIRECRAWL_API_KEY", "test")
    long_content = "A" * (MAX_MARKDOWN_CHARS + 50)
    with httpx.Client(transport=httpx.MockTransport(
        lambda request: httpx.Response(200, json={
            "success": True, "data": {"markdown": long_content, "metadata": {}},
        })
    )) as client:
        result = fetch_firecrawl_page(SPORT, CAPABILITY, URL, client=client, allowed_domains=("nba.com",))
    assert result.ok
    assert len(result.data["markdown"]) == MAX_MARKDOWN_CHARS
    assert result.data["content_truncated"] is True
    assert result.data["content_sha256"] != ""


def test_optional_provider_failure_does_not_change_baseline_status(monkeypatch):
    monkeypatch.setenv("WOW_FIRECRAWL_ENABLED", "true")
    monkeypatch.setenv("WOW_FIRECRAWL_TARGETS_JSON", json.dumps([
        {"sport_key": SPORT, "capability": CAPABILITY, "url": URL},
    ]))
    monkeypatch.setattr(scout, "SPORTSDATAIO_CAPABILITIES", ())
    monkeypatch.setattr(scout, "fetch_firecrawl_page",
                        lambda *args: ResearchFetchResult(
                            False, "FIRECRAWL", SPORT, CAPABILITY, "PUBLIC_WEB_RESEARCH",
                            code="SOURCE_RATE_LIMITED", prediction_authority=False,
                            can_execute=False))
    monkeypatch.setattr(scout, "persist_source_result",
                        lambda r: {"source_status": "BLOCKED", "source_code": r.code})
    response = scout.refresh(sports=(SPORT,))
    assert response["status"] == "COMPLETE"
    assert response["optional_firecrawl"]["status"] == "COMPLETE_WITH_BLOCKERS"
    assert response["optional_firecrawl"]["blocked_snapshots"][0]["source_code"] == "SOURCE_RATE_LIMITED"
    assert response["can_execute"] is False


def test_optional_persistence_failure_is_isolated(monkeypatch):
    monkeypatch.setenv("WOW_FIRECRAWL_ENABLED", "1")
    monkeypatch.setenv("WOW_FIRECRAWL_TARGETS_JSON", json.dumps([
        {"sport_key": SPORT, "capability": CAPABILITY, "url": URL},
    ]))
    monkeypatch.setattr(scout, "SPORTSDATAIO_CAPABILITIES", ())
    monkeypatch.setattr(scout, "fetch_firecrawl_page",
                        lambda *args: ResearchFetchResult(
                            True, "FIRECRAWL", SPORT, CAPABILITY, "PUBLIC_WEB_RESEARCH",
                            data={"markdown": "Evidence"}, code="SOURCE_FETCH_OK"))
    def bad_persistence(_):
        raise RuntimeError("source store unavailable")
    monkeypatch.setattr(scout, "persist_source_result", bad_persistence)
    response = scout.refresh(sports=(SPORT,))
    assert response["status"] == "COMPLETE"
    assert response["optional_firecrawl"]["blocked_snapshots"][0]["source_code"] == "SOURCE_SNAPSHOT_PERSIST_FAILED"


def test_invalid_or_unbounded_config_is_not_executed(monkeypatch):
    monkeypatch.setenv("WOW_FIRECRAWL_ENABLED", "yes")
    monkeypatch.setenv("WOW_FIRECRAWL_TARGETS_JSON", json.dumps([
        {"sport_key": SPORT, "capability": CAPABILITY, "url": URL},
    ] * (MAX_TARGETS_PER_REFRESH + 1)))
    monkeypatch.setattr(scout, "SPORTSDATAIO_CAPABILITIES", ())
    monkeypatch.setattr(scout, "fetch_firecrawl_page", lambda *a: pytest.fail("cap bypassed"))
    response = scout.refresh(sports=(SPORT,))
    assert response["status"] == "COMPLETE"
    assert response["optional_firecrawl"]["status"] == "CONFIG_INVALID"
    assert response["optional_firecrawl"]["can_execute"] is False


def test_source_failure_snapshot_retains_exact_allowlisted_target(monkeypatch):
    monkeypatch.setenv("FIRECRAWL_API_KEY", "test")
    with httpx.Client(transport=httpx.MockTransport(
        lambda request: httpx.Response(429)
    )) as client:
        result = fetch_firecrawl_page(SPORT, CAPABILITY, URL, client=client, allowed_domains=("nba.com",))
    assert result.code == "SOURCE_RATE_LIMITED"
    assert result.data == {"source_url": URL, "evidence_only": True}
    assert result.prediction_authority is False


def test_snapshot_content_identity_is_stable_across_observations(monkeypatch):
    monkeypatch.setenv("FIRECRAWL_API_KEY", "test")
    with httpx.Client(transport=httpx.MockTransport(
        lambda request: httpx.Response(200, json={
            "success": True, "data": {"markdown": "# Same source content", "metadata": {}},
        })
    )) as client:
        result = fetch_firecrawl_page(SPORT, CAPABILITY, URL, client=client, allowed_domains=("nba.com",))
    first_id = snapshot_id_for(result)
    result.observed_at = "2026-12-01T00:00:00Z"
    assert snapshot_id_for(result) == first_id
