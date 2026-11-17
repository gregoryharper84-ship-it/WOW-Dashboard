"""Offline, fail-closed tests for optional cross-sport news discovery."""
import json
from datetime import datetime, timezone

import httpx
import pytest

from v17 import news_feed_research_adapter as news
from v17 import scout_source_refresh as scout
from v17.research_source_adapters import ResearchFetchResult
from v17.research_source_snapshot_store import snapshot_id_for

FEED = "https://feeds.example.org/sports.json"
STORY = "https://official.example.com/updates/injury"
NOW = datetime(2026, 11, 16, 18, 0, tzinfo=timezone.utc)


def _article(**updates):
    article = {
        "url": STORY, "title": "Injury update",
        "published_at": "2026-11-16T17:45:00Z",
        "summary": "Reported roster change; verification pending",
        "event_id": "vendor-123",
    }
    article.update(updates)
    return article


def _client(body, status=200):
    return httpx.Client(transport=httpx.MockTransport(
        lambda request: httpx.Response(status, json=body)))


def test_valid_discovery_is_unverified_nonpredictive(monkeypatch):
    monkeypatch.setattr(news, "_now", lambda: NOW)
    with _client({"articles": [_article()]}) as client:
        result = news.fetch_news_json(
            "basketball_nba", "OFFICIAL_FEED", FEED, client=client,
            allowed_domains=("example.org",), license_ack=True)
    assert result.ok and result.code == "SOURCE_FETCH_OK"
    assert result.prediction_authority is False and result.can_execute is False
    item = result.data["articles"][0]
    assert item["provider_event_id"] == "vendor-123"
    assert item["official_event_identity_verified"] is False
    assert item["publisher_claim_verified"] is False
    assert item["historical_pregame_eligible"] is False
    assert "model_probability" not in result.data
    assert len(item["article_id"]) == 32


def test_all_declared_sports_supported_by_sport_profile(monkeypatch):
    monkeypatch.setattr(news, "_now", lambda: NOW)
    assert len(news.WEIGHT_PROFILES) == 13
    for sport in news.WEIGHT_PROFILES:
        with _client({"articles": [_article()]}) as client:
            result = news.fetch_news_json(
                sport, "PUBLIC_NEWS", FEED, client=client,
                allowed_domains=("example.org",), license_ack=True)
        assert result.ok, sport


@pytest.mark.parametrize("url", [
    "http://feeds.example.org/sports.json",
    "https://feeds.example.org.evil.test/sports.json",
    "https://localhost/sports.json",
    "https://127.0.0.1/sports.json",
    "https://feeds.example.org:9999/sports.json",
    "https://me:pass@feeds.example.org/sports.json",
    "https://feeds.example.org/sports.json?api_key=secret",
])
def test_unsafe_feed_endpoint_blocks_without_network(url):
    with httpx.Client(transport=httpx.MockTransport(
        lambda request: pytest.fail("network call made"))) as client:
        r = news.fetch_news_json(
            "NBA", "PUBLIC_NEWS", url, client=client,
            allowed_domains=("example.org",), license_ack=True)
    assert r.code == "SOURCE_TARGET_NOT_ALLOWLISTED"
    assert r.can_execute is False


def test_licensing_requires_explicit_opt_in_before_http():
    with httpx.Client(transport=httpx.MockTransport(
        lambda request: pytest.fail("unlicensed call"))) as client:
        r = news.fetch_news_json(
            "NBA", "PUBLIC_NEWS", FEED, client=client,
            allowed_domains=("example.org",), license_ack=False)
    assert r.code == "SOURCE_LICENSE_ACK_REQUIRED"


def test_empty_and_bad_published_at_are_not_research_evidence():
    cases = [
        {"articles": []},
        {"articles": [_article(published_at="2026-11-17T12:00:00Z")]},
        {"articles": [_article(published_at="2026-11-16T17:45:00")]},
        {"articles": [_article(url="http://bad.example.net/")]},
        {"articles": [_article(title="")]},
    ]
    for body in cases:
        assert news.normalize_articles(body, observed_at=NOW, provider="TEST") == []


def test_articles_deduplicate_preserving_first_claim_and_snapshot_stability():
    articles = news.normalize_articles(
        {"articles": [_article(), _article(title="syndicated duplicate")]},
        observed_at=NOW, provider="TEST")
    assert len(articles) == 1
    r1 = ResearchFetchResult(
        True, "TEST", "basketball_nba", "news", "PUBLIC_WEB_RESEARCH",
        data={"articles": articles}, observed_at="2026-11-16T18:00:00Z")
    r2 = ResearchFetchResult(
        True, "TEST", "basketball_nba", "news", "PUBLIC_WEB_RESEARCH",
        data={"articles": articles}, observed_at="2026-11-16T19:00:00Z")
    assert snapshot_id_for(r1) == snapshot_id_for(r2)


@pytest.mark.parametrize("status,code", [
    (301, "SOURCE_REDIRECT_NOT_ALLOWED"),
    (401, "SOURCE_AUTH_FAILED"),
    (429, "SOURCE_RATE_LIMITED"),
    (503, "SOURCE_UPSTREAM_UNAVAILABLE"),
])
def test_http_failures_are_optional_and_typed(status, code):
    with _client({"error": "nope"}, status) as client:
        r = news.fetch_news_json(
            "NBA", "PUBLIC_NEWS", FEED, client=client,
            allowed_domains=("example.org",), license_ack=True)
    assert not r.ok and r.code == code
    assert r.prediction_authority is False and r.can_execute is False


def test_large_response_is_bounded():
    payload = {"articles": [_article(summary="x" * news.MAX_NEWS_BODY_BYTES)]}
    with _client(payload) as client:
        r = news.fetch_news_json(
            "NBA", "PUBLIC_NEWS", FEED, client=client,
            allowed_domains=("example.org",), license_ack=True)
    assert r.code == "SOURCE_BODY_TOO_LARGE"


def test_invalid_feed_document_does_not_fabricate_news():
    with _client({"data": [{"headline": "unsupported provider shape"}]}) as client:
        r = news.fetch_news_json(
            "NBA", "PUBLIC_NEWS", FEED, client=client,
            allowed_domains=("example.org",), license_ack=True)
    assert r.code == "SOURCE_OUTPUT_INVALID"


def test_missing_feed_target_config_is_typed_not_healthy(monkeypatch):
    monkeypatch.setenv("WOW_NEWS_FEED_ENABLED", "true")
    monkeypatch.setenv("WOW_NEWS_FEED_TARGETS_JSON", "[]")
    monkeypatch.setattr(scout, "SPORTSDATAIO_CAPABILITIES", ())
    r = scout.refresh(sports=("basketball_nba",))
    assert r["optional_news"]["status"] == "CONFIG_INVALID"
    assert r["optional_news"]["source_code"] == "SOURCE_TARGETS_CONFIG_INVALID"
    assert r["status"] == "COMPLETE"  # independent structured baseline
    assert r["prediction_authority"] is False and r["can_execute"] is False


def test_optional_news_success_persists_as_source_snapshot(monkeypatch):
    monkeypatch.setenv("WOW_NEWS_FEED_ENABLED", "true")
    monkeypatch.setenv("WOW_NEWS_FEED_TARGETS_JSON", json.dumps([
        {"sport_key": "basketball_nba", "provider": "PUBLIC_NEWS", "url": FEED}]))
    monkeypatch.setattr(scout, "SPORTSDATAIO_CAPABILITIES", ())
    monkeypatch.setattr(scout, "fetch_news_json", lambda sport, provider, url:
                        ResearchFetchResult(True, provider, sport, "news", "PUBLIC_WEB_RESEARCH",
                                            data={"articles": [_article()]}, code="SOURCE_FETCH_OK",
                                            prediction_authority=False, can_execute=False))
    monkeypatch.setattr(scout, "persist_source_result", lambda result: {
        "source_status": "OK", "source_code": result.code,
        "prediction_authority": result.prediction_authority,
        "can_execute": result.can_execute,
    })
    r = scout.refresh(sports=("basketball_nba",))
    assert r["optional_news"]["status"] == "COMPLETE"
    assert len(r["optional_news"]["successful_snapshots"]) == 1
    assert r["optional_news"]["successful_snapshots"][0]["can_execute"] is False


def test_optional_news_failure_and_persistence_errors_do_not_block_core(monkeypatch):
    monkeypatch.setenv("WOW_NEWS_FEED_ENABLED", "1")
    monkeypatch.setenv("WOW_NEWS_FEED_TARGETS_JSON", json.dumps([
        {"sport_key": "basketball_nba", "provider": "PUBLIC_NEWS", "url": FEED}]))
    monkeypatch.setattr(scout, "SPORTSDATAIO_CAPABILITIES", ())
    monkeypatch.setattr(scout, "fetch_news_json", lambda *args:
                        ResearchFetchResult(True, "PUBLIC_NEWS", "basketball_nba", "news",
                                            "PUBLIC_WEB_RESEARCH", data={"articles": []}))
    monkeypatch.setattr(scout, "persist_source_result",
                        lambda r: (_ for _ in ()).throw(RuntimeError("secret")))
    r = scout.refresh(sports=("basketball_nba",))
    assert r["status"] == "COMPLETE"
    assert r["optional_news"]["status"] == "COMPLETE_WITH_BLOCKERS"
    assert r["optional_news"]["blocked_snapshots"][0]["source_code"] == "SOURCE_SNAPSHOT_PERSIST_FAILED"
    assert "secret" not in str(r)


def test_news_disabled_by_default(monkeypatch):
    monkeypatch.delenv("WOW_NEWS_FEED_ENABLED", raising=False)
    monkeypatch.setattr(scout, "SPORTSDATAIO_CAPABILITIES", ())
    monkeypatch.setattr(scout, "fetch_news_json", lambda *a: pytest.fail("news unexpectedly enabled"))
    r = scout.refresh(sports=("basketball_nba",))
    assert "optional_news" not in r
