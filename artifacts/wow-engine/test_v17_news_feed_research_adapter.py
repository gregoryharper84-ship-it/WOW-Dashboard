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


def _streamed_json_response(body, status=200):
    # MockTransport's Response(json=...) is pre-consumed and cannot be read via
    # iter_raw. Exercise the actual production streaming contract instead.
    return httpx.Response(status, headers={"Content-Type": "application/json"},
                          stream=httpx.ByteStream(json.dumps(body).encode()))


def _client(body, status=200):
    return httpx.Client(transport=httpx.MockTransport(
        lambda request: _streamed_json_response(body, status)))


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
    monkeypatch.setattr(scout, "persist_immutable_news_source_result", lambda result: {
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
    monkeypatch.setattr(scout, "persist_immutable_news_source_result",
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
    monkeypatch.setattr(scout, "persist_immutable_news_source_result",
                        lambda r: {"source_status": "OK", "source_code": r.code})
    r = scout.refresh(sports=("basketball_nba", "americanfootball_nfl"))
    assert len(calls) == 1
    assert r["optional_news"]["status"] == "COMPLETE_WITH_BLOCKERS"
    assert r["optional_news"]["blocked_snapshots"][0]["source_code"] == "SOURCE_GDELT_RATE_BUDGET_EXCEEDED"


def test_scout_schedule_degrades_without_paid_source_but_writes_a_receipt():
    from pathlib import Path
    import yaml
    workflow_path = (Path(__file__).resolve().parents[2] /
                     ".github/workflows/wow-v17-scout-source-refresh.yml")
    content = workflow_path.read_text()
    data = yaml.safe_load(content)
    steps = data["jobs"]["refresh"]["steps"]
    by_name = {x.get("name"): x for x in steps}
    preflight = by_name["Check source credentials"]["run"]
    assert 'if [ -n "$WOW_SCOUT_DATABASE_URL" ]; then' in preflight
    assert '&& [ -n "$WOW_SPORTSDATAIO_API_KEY" ]' not in preflight
    assert "Write blocked configuration receipt" in by_name
    blocked = by_name["Write blocked configuration receipt"]["run"]
    assert '"status": "BLOCKED_SOURCE_CONFIGURATION"' in blocked
    assert '"source_code": "SOURCE_DATABASE_UNCONFIGURED"' in blocked
    upload = by_name["Upload source refresh receipt"]
    assert upload["if"] == "always()"
    assert upload["with"]["if-no-files-found"] == "error"



def test_scheduled_news_discovery_declares_every_sport():
    assert len(scout.DEFAULT_SPORTS) == 13
    assert {news.normalize_sport(x) for x in scout.DEFAULT_SPORTS} == set(news.WEIGHT_PROFILES)


def test_news_availability_timestamp_is_after_completed_fetch(monkeypatch):
    moments = iter([
        datetime(2026, 11, 16, 18, 0, 0, 0, tzinfo=timezone.utc),
        datetime(2026, 11, 16, 18, 0, 1, 3, tzinfo=timezone.utc),
    ])
    monkeypatch.setattr(news, "_now", lambda: next(moments))
    with _client({"articles": [_article()]}) as client:
        result = news.fetch_news_json(
            "NBA", "PUBLIC_NEWS", FEED, client=client,
            allowed_domains=("example.org",), license_ack=True)
    assert result.ok
    assert result.observed_at == "2026-11-16T18:00:01.000003Z"
    assert result.data["source_observed_at"] == result.observed_at
    assert result.data["articles"][0]["historical_pregame_eligible"] is False


def test_news_snapshot_ids_are_time_specific_and_immutable_insert_only(monkeypatch):
    from v17 import research_source_snapshot_store as store

    base = {
        "source_observed_at": "2026-11-16T18:00:00Z",
        "articles": [_article()],
    }
    a = ResearchFetchResult(
        True, "GDELT", "basketball_nba", "news", "PUBLIC_WEB_RESEARCH",
        data=dict(base), code="SOURCE_FETCH_OK", observed_at="2026-11-16T18:00:00Z")
    b = ResearchFetchResult(
        True, "GDELT", "basketball_nba", "news", "PUBLIC_WEB_RESEARCH",
        data={**base, "source_observed_at": "2026-11-16T18:01:00Z"},
        code="SOURCE_FETCH_OK", observed_at="2026-11-16T18:01:00Z")
    assert store.news_snapshot_id_for(a) != store.news_snapshot_id_for(b)
    assert store.news_snapshot_id_for(a) == store.news_snapshot_id_for(a)

    records = []
    class Cursor:
        def __enter__(self): return self
        def __exit__(self, *_): return False
        def execute(self, sql, params): records.append((sql, params))
    class Conn:
        def __enter__(self): return self
        def __exit__(self, *_): return False
        def cursor(self): return Cursor()
        def commit(self): records.append(("commit", None))
    monkeypatch.setattr(store, "database_url", lambda: "postgresql://test-only")
    monkeypatch.setattr(store.psycopg, "connect", lambda url: Conn())
    first = store.persist_immutable_news_source_result(a)
    second = store.persist_immutable_news_source_result(b)
    assert first["snapshot_id"] != second["snapshot_id"]
    assert first["source_observed_at"] == "2026-11-16T18:00:00Z"
    assert all("on conflict (snapshot_id) do nothing" in sql.lower()
               and "do update" not in sql.lower()
               for sql, _ in records if sql != "commit")
    assert first["prediction_authority"] is False
    assert first["can_execute"] is False


@pytest.mark.parametrize("observed_at", [
    None, "not-an-iso-date", "2026-11-16T18:00:00",
])
def test_news_snapshot_rejects_invalid_temporal_provenance(observed_at):
    from v17.research_source_snapshot_store import news_snapshot_id_for
    candidate = ResearchFetchResult(
        True, "GDELT", "basketball_nba", "news", "PUBLIC_WEB_RESEARCH",
        data={"articles": []}, observed_at=observed_at)
    with pytest.raises(ValueError, match="NEWS_RECEIPT"):
        news_snapshot_id_for(candidate)



def test_private_key_sent_only_to_exact_provider_and_hostname(monkeypatch):
    monkeypatch.setenv("WOW_NEWS_FEED_API_KEY", "never-share-license-token")
    monkeypatch.setenv("WOW_NEWS_FEED_API_KEY_PROVIDER", "LICENSED_NEWS")
    monkeypatch.setenv("WOW_NEWS_FEED_API_KEY_HOST", "feeds.example.org")
    seen = []
    def handler(request):
        seen.append((str(request.url), request.headers.get("Authorization")))
        if request.url.host == "api.gdeltproject.org":
            return _streamed_json_response({"articles": [
                {"url": STORY, "title": "Discovered",
                 "seendate": "20261116T174500Z"}]})
        return _streamed_json_response({"articles": [_article()]})
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        permitted = news.fetch_news_json(
            "NBA", "LICENSED_NEWS", FEED, client=client,
            allowed_domains=("example.org",), license_ack=True)
        public = news.fetch_news_json(
            "NBA", "GDELT",
            "https://api.gdeltproject.org/api/v2/doc/doc?query=NBA&mode=ArtList&format=json",
            client=client, allowed_domains=("gdeltproject.org",), license_ack=True)
    assert permitted.ok and public.ok
    assert seen[0][1] == "Bearer never-share-license-token"
    assert seen[1][1] is None
    assert all("never-share-license-token" not in str(x.to_dict()) for x in (permitted, public))


def test_origin_mismatch_fails_before_network_and_does_not_leak(monkeypatch):
    monkeypatch.setenv("WOW_NEWS_FEED_API_KEY", "private-vendor-secret")
    monkeypatch.setenv("WOW_NEWS_FEED_API_KEY_PROVIDER", "LICENSED_NEWS")
    monkeypatch.setenv("WOW_NEWS_FEED_API_KEY_HOST", "different.example.org")
    with httpx.Client(transport=httpx.MockTransport(
        lambda _: pytest.fail("unbound credential target was fetched"))) as client:
        result = news.fetch_news_json(
            "NBA", "LICENSED_NEWS", FEED, client=client,
            allowed_domains=("example.org",), license_ack=True)
    assert result.code == "SOURCE_CREDENTIAL_ORIGIN_MISMATCH"
    assert "private-vendor-secret" not in str(result.to_dict())


def test_missing_credential_binding_fails_closed(monkeypatch):
    monkeypatch.setenv("WOW_NEWS_FEED_API_KEY", "private-vendor-secret")
    monkeypatch.delenv("WOW_NEWS_FEED_API_KEY_PROVIDER", raising=False)
    monkeypatch.delenv("WOW_NEWS_FEED_API_KEY_HOST", raising=False)
    with httpx.Client(transport=httpx.MockTransport(
        lambda _: pytest.fail("unscoped vendor token target fetched"))) as client:
        result = news.fetch_news_json(
            "NBA", "LICENSED_NEWS", FEED, client=client,
            allowed_domains=("example.org",), license_ack=True)
    assert result.code == "SOURCE_CREDENTIAL_SCOPE_UNCONFIGURED"


def test_public_news_provider_never_receives_other_providers_token(monkeypatch):
    monkeypatch.setenv("WOW_NEWS_FEED_API_KEY", "private-vendor-secret")
    monkeypatch.setenv("WOW_NEWS_FEED_API_KEY_PROVIDER", "LICENSED_NEWS")
    monkeypatch.setenv("WOW_NEWS_FEED_API_KEY_HOST", "feeds.example.org")
    def handler(req):
        assert req.headers.get("Authorization") is None
        return _streamed_json_response({"articles": [_article()]})
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        result = news.fetch_news_json(
            "NBA", "PUBLIC_NEWS", "https://public.example.org/stories.json",
            client=client, allowed_domains=("example.org",), license_ack=True)
    assert result.ok


def test_compressed_response_rejected_without_decoder_allocation():
    import gzip
    # Highly compressible payload: 16+ MiB decoded from a tiny input.
    bomb = gzip.compress(json.dumps({"articles": [
        _article(summary="x" * (16 * 1024 * 1024))]}).encode())
    assert len(bomb) < news.MAX_NEWS_BODY_BYTES
    with httpx.Client(transport=httpx.MockTransport(
        lambda _: httpx.Response(200, content=bomb,
                                 headers={"Content-Encoding": "gzip"}))) as client:
        result = news.fetch_news_json(
            "NBA", "PUBLIC_NEWS", FEED, client=client,
            allowed_domains=("example.org",), license_ack=True)
    assert result.code == "SOURCE_CONTENT_ENCODING_UNSUPPORTED"
    assert result.can_execute is False


def test_oversized_content_length_rejected_without_streaming():
    with httpx.Client(transport=httpx.MockTransport(
        lambda _: httpx.Response(200, content=b"x",
                                 headers={"Content-Length": str(news.MAX_NEWS_BODY_BYTES + 1)}))) as client:
        result = news.fetch_news_json(
            "NBA", "PUBLIC_NEWS", FEED, client=client,
            allowed_domains=("example.org",), license_ack=True)
    assert result.code == "SOURCE_BODY_TOO_LARGE"


def test_repeated_same_failure_has_unique_sql_content_hash(monkeypatch):
    from v17 import research_source_snapshot_store as store
    moments = iter([
        datetime(2026, 11, 16, 18, 0, 0, 12345, tzinfo=timezone.utc),
        datetime(2026, 11, 16, 18, 0, 1, 12345, tzinfo=timezone.utc),
    ])
    monkeypatch.setattr(news, "_now", lambda: next(moments))
    with _client({"error": "rate limited"}, 429) as client:
        first = news.fetch_news_json(
            "NBA", "PUBLIC_NEWS", FEED, client=client,
            allowed_domains=("example.org",), license_ack=True)
        second = news.fetch_news_json(
            "NBA", "PUBLIC_NEWS", FEED, client=client,
            allowed_domains=("example.org",), license_ack=True)
    assert first.code == second.code == "SOURCE_RATE_LIMITED"
    assert first.data["source_observed_at"] == first.observed_at
    assert second.data["source_observed_at"] == second.observed_at
    assert store._payload_hash(first.data) != store._payload_hash(second.data)
    assert store.news_snapshot_id_for(first) != store.news_snapshot_id_for(second)


def test_news_store_rejects_absent_captured_timestamp_payload():
    from v17 import research_source_snapshot_store as store
    failed = ResearchFetchResult(
        False, "GDELT", "basketball_nba", "news", "PUBLIC_WEB_RESEARCH",
        data=None, code="SOURCE_RATE_LIMITED",
        observed_at="2026-11-16T18:00:00Z")
    with pytest.raises(ValueError, match="NEWS_RECEIPT_PAYLOAD_PROVENANCE_MISSING"):
        store.persist_immutable_news_source_result(failed)


def test_credentials_are_bound_in_actual_scheduled_workflow():
    from pathlib import Path
    content = (Path(__file__).resolve().parents[2] /
               ".github/workflows/wow-v17-scout-source-refresh.yml").read_text()
    assert "WOW_NEWS_FEED_API_KEY_PROVIDER:" in content
    assert "WOW_NEWS_FEED_API_KEY_HOST:" in content



def test_injected_redirect_following_client_cannot_forward_vendor_token(monkeypatch):
    monkeypatch.setenv("WOW_NEWS_FEED_API_KEY", "secret-for-feeds-only")
    monkeypatch.setenv("WOW_NEWS_FEED_API_KEY_PROVIDER", "LICENSED_NEWS")
    monkeypatch.setenv("WOW_NEWS_FEED_API_KEY_HOST", "feeds.example.org")
    seen_hosts = []
    def handler(request):
        seen_hosts.append(request.url.host)
        return httpx.Response(302, headers={"Location": "https://evil.example.net/collect"})
    with httpx.Client(transport=httpx.MockTransport(handler), follow_redirects=True) as client:
        result = news.fetch_news_json(
            "NBA", "LICENSED_NEWS", FEED, client=client,
            allowed_domains=("example.org",), license_ack=True)
    assert result.code == "SOURCE_REDIRECT_NOT_ALLOWED"
    assert seen_hosts == ["feeds.example.org"]
