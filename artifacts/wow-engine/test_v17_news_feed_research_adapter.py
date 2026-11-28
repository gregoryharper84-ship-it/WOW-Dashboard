"""Public-only, DB-free GDELT news discovery regression tests."""
import gzip
import inspect
import json
from datetime import datetime, timedelta, timezone
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest

from v17 import news_feed_research_adapter as news
from v17 import public_gdelt_discovery as runner

NOW = datetime(2026, 11, 16, 18, 0, tzinfo=timezone.utc)
ENDPOINT = ("https://api.gdeltproject.org/api/v2/doc/doc?"
            "query=NBA%20injury&mode=ArtList&format=json&maxrecords=20&timespan=24h")
ARTICLE = "https://example.org/injury-report"
STORY = {"url": ARTICLE, "title": "Injury news", "seendate": "20261116T174500Z"}


def _response(body=None, status=200, headers=None):
    payload = json.dumps(body if body is not None else {"articles": [STORY]}).encode()
    return httpx.Response(
        status, headers={"Content-Type": "application/json", **(headers or {})},
        stream=httpx.ByteStream(payload))


def _client(handler=None):
    return httpx.Client(transport=httpx.MockTransport(
        handler or (lambda request: _response())))


def test_public_source_returns_unverified_evidence_only(monkeypatch):
    monkeypatch.setattr(news, "_now", lambda: NOW)
    with _client() as client:
        result = news.fetch_gdelt_news("basketball_nba", ENDPOINT, client=client)
    assert result.ok and result.code == "SOURCE_FETCH_OK"
    assert result.can_execute is False and result.prediction_authority is False
    assert result.data["verified_event_count"] == 0
    assert result.data["source_observed_at"] == result.observed_at
    assert result.data["attribution"] == "The GDELT Project (https://www.gdeltproject.org/)"
    item = result.data["articles"][0]
    assert item["source_url"] == ARTICLE
    assert item["source_published_at_claim"] is None
    assert item["source_discovered_at_claim"] == "2026-11-16T17:45:00Z"
    assert item["official_event_identity_verified"] is False
    assert item["publisher_claim_verified"] is False
    assert item["historical_pregame_eligible"] is False


def test_every_supported_sport_has_one_query_and_active_rotation_is_bounded():
    assert len(runner.SPORT_QUERIES) == len(news.WEIGHT_PROFILES) == 13
    assert {news.normalize_sport(x) for x, _ in runner.SPORT_QUERIES} == set(news.WEIGHT_PROFILES)
    start = datetime(2026, 11, 16, 0, tzinfo=timezone.utc)
    active = set(runner.active_sport_keys(start))
    sequence = [runner.selection(start + timedelta(hours=6 * i))[0] for i in range(len(active))]
    assert set(sequence) == active
    assert "baseball_mlb" not in active  # November off-season not daily-rotated


@pytest.mark.parametrize("url", [
    "http://api.gdeltproject.org/api/v2/doc/doc?query=NBA&mode=ArtList&format=json",
    "https://evil.example/api/v2/doc/doc?query=NBA&mode=ArtList&format=json",
    "https://api.gdeltproject.org.evil.example/api/v2/doc/doc?query=NBA&mode=ArtList&format=json",
    "https://127.0.0.1/api/v2/doc/doc?query=NBA&mode=ArtList&format=json",
    "https://me:pass@api.gdeltproject.org/api/v2/doc/doc?query=NBA&mode=ArtList&format=json",
    "https://api.gdeltproject.org:8080/api/v2/doc/doc?query=NBA&mode=ArtList&format=json",
    "https://api.gdeltproject.org/api/v2/doc/doc?query=NBA&mode=ArtList&format=json&api_key=abc",
    "https://api.gdeltproject.org/api/v2/doc/doc?query=NBA&mode=TimelineVol&format=json",
    "https://api.gdeltproject.org/api/v2/doc/doc?mode=ArtList&format=json",
    "https://api.gdeltproject.org/api/v2/doc/doc?query=NBA&mode=ArtList&format=RSS",
    "https://api.gdeltproject.org/api/v2/doc/doc?query=NBA&mode=ArtList&format=json&maxrecords=200",
])
def test_invalid_endpoints_rejected_without_request(url):
    with _client(lambda request: pytest.fail("unexpected network call")) as client:
        result = news.fetch_gdelt_news("basketball_nba", url, client=client)
    assert not result.ok and result.code in ("SOURCE_GDELT_QUERY_INVALID",
                                              "SOURCE_TARGET_NOT_ALLOWLISTED")


def test_public_discovery_never_sends_tokens_or_cookies(monkeypatch):
    monkeypatch.setattr(news, "_now", lambda: NOW)
    monkeypatch.setenv("WOW_NEWS_FEED_API_KEY", "a-commercial-token")
    monkeypatch.setenv("WOW_NEWS_FEED_API_KEY_PROVIDER", "GDELT")
    monkeypatch.setenv("WOW_NEWS_FEED_API_KEY_HOST", "api.gdeltproject.org")
    def handler(request):
        assert request.headers.get("Authorization") is None
        assert request.headers.get("Cookie") is None
        assert request.headers.get("Accept-Encoding") == "identity"
        return _response()
    with _client(handler) as client:
        assert news.fetch_gdelt_news("basketball_nba", ENDPOINT, client=client).ok
    assert "WOW_NEWS_FEED_API_KEY" not in inspect.getsource(news)


def test_injected_follow_redirects_client_cannot_redirect():
    seen = []
    def handler(request):
        seen.append(request.url.host)
        return httpx.Response(302, headers={"Location": "https://evil.example/steal"})
    with httpx.Client(transport=httpx.MockTransport(handler), follow_redirects=True) as client:
        r = news.fetch_gdelt_news("basketball_nba", ENDPOINT, client=client)
    assert r.code == "SOURCE_REDIRECT_NOT_ALLOWED" and seen == [news.GDELT_HOST]


@pytest.mark.parametrize("status,code", [
    (429, "SOURCE_RATE_LIMITED"), (503, "SOURCE_UPSTREAM_UNAVAILABLE"),
    (403, "SOURCE_UPSTREAM_REJECTED")])
def test_source_failures_are_typed_and_nonpredictive(status, code):
    with _client(lambda request: _response({"error": "unavailable"}, status)) as client:
        result = news.fetch_gdelt_news("basketball_nba", ENDPOINT, client=client)
    assert result.code == code and result.ok is False
    assert result.can_execute is False and result.prediction_authority is False
    assert result.data["source_code"] == code


def test_gzip_bomb_is_rejected_before_decompression():
    huge = gzip.compress(b"x" * (16 * 1024 * 1024))
    assert len(huge) < news.MAX_NEWS_BODY_BYTES
    with _client(lambda request: httpx.Response(
        200, headers={"Content-Encoding": "gzip"},
        stream=httpx.ByteStream(huge))) as client:
        result = news.fetch_gdelt_news("basketball_nba", ENDPOINT, client=client)
    assert result.code == "SOURCE_CONTENT_ENCODING_UNSUPPORTED"


@pytest.mark.parametrize("headers,body", [
    ({"Content-Length": "5000000"}, b"x"),
    ({}, b"x" * (news.MAX_NEWS_BODY_BYTES + 1)),
])
def test_large_raw_responses_rejected(headers, body):
    with _client(lambda request: httpx.Response(200, headers=headers,
                                                stream=httpx.ByteStream(body))) as client:
        result = news.fetch_gdelt_news("basketball_nba", ENDPOINT, client=client)
    assert result.code == "SOURCE_BODY_TOO_LARGE"


def test_bad_data_and_missing_seen_timestamp_not_promoted(monkeypatch):
    monkeypatch.setattr(news, "_now", lambda: NOW)
    for payload in (
        {"articles": [{"url": ARTICLE, "title": "headline"}]},
        {"articles": [{**STORY, "seendate": "20261117T174500Z"}]},
        {"articles": []},
    ):
        with _client(lambda request: _response(payload)) as client:
            result = news.fetch_gdelt_news("basketball_nba", ENDPOINT, client=client)
        assert result.code == "SOURCE_NO_VALID_NEWS"
        assert result.ok is False


def test_timestamp_is_recorded_after_http_response(monkeypatch):
    calls = []
    def handler(request):
        calls.append("fetched")
        return _response()
    def clock():
        assert calls == ["fetched"]
        calls.append("captured")
        return NOW
    monkeypatch.setattr(news, "_now", clock)
    with _client(handler) as client:
        result = news.fetch_gdelt_news("basketball_nba", ENDPOINT, client=client)
    assert result.ok and calls == ["fetched", "captured"]


def test_runner_produces_receipt_without_database(monkeypatch):
    monkeypatch.setattr(news, "_now", lambda: NOW)
    with _client() as client:
        r = runner.discover(NOW, sport="basketball_nba", client=client)
    assert r["source_status"] == "OK"
    assert r["source_code"] == "SOURCE_FETCH_OK"
    assert len(r["articles"]) == 1
    assert r["model_feature_eligible"] is False
    assert r["historical_pregame_eligible"] is False
    assert r["can_execute"] is False
    assert r["prediction_authority"] is False
    assert "psycopg" not in inspect.getsource(runner)


def test_invalid_runner_sport_is_blocked_without_network():
    r = runner.discover(NOW, sport="not-a-sport")
    assert r["source_status"] == "BLOCKED" and r["source_code"] == "SOURCE_TARGET_INVALID"
    assert r["articles"] == [] and r["can_execute"] is False


def test_workflow_public_news_runs_before_database_credential_preflight():
    from pathlib import Path
    import yaml
    path = Path(__file__).resolve().parents[2] / ".github/workflows/wow-v17-scout-source-refresh.yml"
    raw = path.read_text()
    workflow = yaml.safe_load(raw)
    steps = workflow["jobs"]["refresh"]["steps"]
    names = [x.get("name") for x in steps]
    assert names.index("Public GDELT research discovery (independent of database)") < names.index("Check source credentials")
    gdelt_step = steps[names.index("Public GDELT research discovery (independent of database)")]
    assert gdelt_step["continue-on-error"] is True
    assert "public_gdelt_discovery.py" in gdelt_step["run"]
    assert "WOW_NEWS_FEED_API_KEY" not in raw
    upload = steps[names.index("Upload public news evidence receipt")]
    assert upload["if"] == "always()"
    assert upload["with"]["retention-days"] == 90
