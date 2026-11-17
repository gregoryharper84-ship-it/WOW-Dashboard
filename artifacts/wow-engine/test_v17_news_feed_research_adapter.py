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


def test_gdelt_public_artlist_maps_seen_date_not_publication(monkeypatch):
    monkeypatch.setattr(news, "_now", lambda: NOW)
    endpoint = ("https://api.gdeltproject.org/api/v2/doc/doc?"
                "query=NBA%20injury&mode=ArtList&format=json&maxrecords=5&timespan=24h")
    body = {"articles": [{"url": STORY, "title": "Potential injury",
                          "seendate": "20261116T174500Z", "domain": "official.example.com"}]}
    with _client(body) as client:
        result = news.fetch_news_json(
            "basketball_nba", "GDELT", endpoint, client=client,
            allowed_domains=("gdeltproject.org",), license_ack=True)
    assert result.ok
    article = result.data["articles"][0]
    assert article["source_published_at_claim"] is None
    assert article["source_discovered_at_claim"] == "2026-11-16T17:45:00Z"
    assert article["official_event_identity_verified"] is False
    assert article["historical_pregame_eligible"] is False


@pytest.mark.parametrize("endpoint", [
    "https://evil.example.com/api/v2/doc/doc?query=NBA&mode=ArtList&format=json",
    "https://api.gdeltproject.org/api/v2/doc/doc?query=NBA&mode=TimelineVol&format=json",
    "https://api.gdeltproject.org/api/v2/doc/doc?query=NBA&mode=ArtList&format=RSS",
    "https://api.gdeltproject.org/api/v2/doc/doc?mode=ArtList&format=json",
])
def test_gdelt_query_contract_fail_closed(endpoint):
    with httpx.Client(transport=httpx.MockTransport(
        lambda request: pytest.fail("invalid GDELT query fetched"))) as client:
        result = news.fetch_news_json(
            "NBA", "GDELT", endpoint, client=client,
            allowed_domains=("gdeltproject.org", "example.com"), license_ack=True)
    assert result.ok is False


def test_gdelt_future_or_missing_seen_time_not_accepted():
    assert news.normalize_articles(
        {"articles": [{"url": STORY, "title": "future", "seendate": "20261117T174500Z"}]},
        observed_at=NOW, provider="GDELT") == []
    assert news.normalize_articles(
        {"articles": [{"url": STORY, "title": "missing", "published_at": "2026-11-16T17:45:00Z"}]},
        observed_at=NOW, provider="GDELT") == []


def test_enabled_news_target_outside_selected_sports_is_not_complete(monkeypatch):
    monkeypatch.setenv("WOW_NEWS_FEED_ENABLED", "true")
    monkeypatch.setenv("WOW_NEWS_FEED_TARGETS_JSON", json.dumps([
        {"sport_key": "basketball_nba", "provider": "PUBLIC_NEWS", "url": FEED}]))
    monkeypatch.setattr(scout, "SPORTSDATAIO_CAPABILITIES", ())
    monkeypatch.setattr(scout, "fetch_news_json", lambda *args: pytest.fail("outside sport scope"))
    r = scout.refresh(sports=("americanfootball_nfl",))
    assert r["optional_news"]["status"] == "NO_MATCHING_TARGETS"


def test_second_gdelt_query_is_budget_blocked_without_fetch(monkeypatch):
    monkeypatch.setenv("WOW_NEWS_FEED_ENABLED", "true")
    monkeypatch.setenv("WOW_NEWS_FEED_TARGETS_JSON", json.dumps([
        {"sport_key": "basketball_nba", "provider": "GDELT",
         "url": "https://api.gdeltproject.org/api/v2/doc/doc?query=NBA&mode=ArtList&format=json"},
        {"sport_key": "americanfootball_nfl", "provider": "GDELT",
         "url": "https://api.gdeltproject.org/api/v2/doc/doc?query=NFL&mode=ArtList&format=json"},
    ]))
    monkeypatch.setattr(scout, "SPORTSDATAIO_CAPABILITIES", ())
    calls = []
    def fake_fetch(sport, provider, url):
        calls.append(sport)
        return ResearchFetchResult(
            True, "GDELT", sport, "news", "PUBLIC_WEB_RESEARCH",
            data={"articles": [_article()]}, code="SOURCE_FETCH_OK")
    monkeypatch.setattr(scout, "fetch_news_json", fake_fetch)
    monkeypatch.setattr(scout, "persist_source_result",
                        lambda r: {"source_status": "OK", "source_code": r.code})
    r = scout.refresh(sports=("basketball_nba", "americanfootball_nfl"))
    assert len(calls) == 1
    assert r["optional_news"]["status"] == "COMPLETE_WITH_BLOCKERS"
    assert r["optional_news"]["blocked_snapshots"][0]["source_code"] == "SOURCE_GDELT_RATE_BUDGET_EXCEEDED"
