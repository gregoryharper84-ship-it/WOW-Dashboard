import httpx

from v17.research_source_adapters import adapter_health, capability_for, sportsdataio_fetch


def test_provider_is_disabled_without_credential(monkeypatch):
    monkeypatch.delenv("SPORTSDATAIO_API_KEY", raising=False)
    monkeypatch.delenv("WOW_SPORTSDATAIO_API_KEY", raising=False)
    result = sportsdataio_fetch("americanfootball_nfl", "injuries")
    assert result.ok is False
    assert result.code == "SOURCE_CREDENTIAL_UNCONFIGURED"
    assert result.prediction_authority is False
    assert result.can_execute is False


def test_known_provider_gap_is_explicit_not_guessed(monkeypatch):
    monkeypatch.setenv("SPORTSDATAIO_API_KEY", "test")
    result = sportsdataio_fetch("americanfootball_ncaaf", "depth_charts")
    assert result.ok is False
    assert result.code == "SOURCE_CAPABILITY_UNSUPPORTED"
    cap = capability_for("americanfootball_ncaaf", "depth_charts")
    assert cap is not None
    assert cap.supported is False
    assert "does not supply" in (cap.limitation or "")


def test_wnba_pregame_lineup_gap_is_explicit(monkeypatch):
    monkeypatch.setenv("SPORTSDATAIO_API_KEY", "test")
    result = sportsdataio_fetch("basketball_wnba", "starting_lineups")
    assert result.ok is False
    assert result.code == "SOURCE_CAPABILITY_UNSUPPORTED"


def test_configurable_capability_fails_closed_until_path_configured(monkeypatch):
    monkeypatch.setenv("SPORTSDATAIO_API_KEY", "test")
    monkeypatch.delenv("WOW_SPORTSDATAIO_BASKETBALL_NBA_DEPTH_CHARTS_PATH", raising=False)
    result = sportsdataio_fetch("basketball_nba", "depth_charts")
    assert result.ok is False
    assert result.code == "SOURCE_ENDPOINT_UNCONFIGURED"


def test_verified_endpoint_uses_header_auth_and_returns_evidence_only(monkeypatch):
    monkeypatch.setenv("SPORTSDATAIO_API_KEY", "secret-test")
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["key"] = request.headers.get("Ocp-Apim-Subscription-Key")
        return httpx.Response(200, json=[{"PlayerID": 1, "InjuryStatus": "Questionable"}])

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        result = sportsdataio_fetch("americanfootball_nfl", "injuries", client=client)
    assert result.ok is True
    assert captured["url"].endswith("/v3/nfl/projections/json/InjuredPlayers")
    assert captured["key"] == "secret-test"
    assert result.data[0]["InjuryStatus"] == "Questionable"
    assert result.prediction_authority is False
    assert result.can_execute is False


def test_mlb_lineup_path_requires_date_and_never_leaks_key(monkeypatch):
    monkeypatch.setenv("SPORTSDATAIO_API_KEY", "secret-test")
    missing = sportsdataio_fetch("baseball_mlb", "starting_lineups")
    assert missing.ok is False
    assert missing.code == "SOURCE_PATH_PARAMETER_MISSING"
    assert "secret-test" not in str(missing.to_dict())


def test_health_reports_configuration_without_secret_value(monkeypatch):
    monkeypatch.setenv("SPORTSDATAIO_API_KEY", "secret-test")
    health = adapter_health()
    assert health["credential_configured"] is True
    assert health["prediction_authority"] is False
    assert health["can_execute"] is False
    assert "secret-test" not in str(health)


# Governed optional news-source registry regressions.
# This existing Scout source suite runs in protected CI; no extra workflow is needed.
from datetime import datetime, timezone

import pytest

from v17.multi_source_news_registry import (
    SOURCES, source_inventory, normalize_newsapi_headlines,
    normalize_espn_rss, source_receipt,
)

AT = datetime(2026, 11, 20, 12, 0, tzinfo=timezone.utc)


def test_all_requested_providers_visible_with_no_false_activation():
    names = {row["provider"] for row in source_inventory()}
    assert names == {"GDELT", "ESPN_RSS", "NEWSAPI",
                     "SPORTSDATAIO_GLOBAL", "ALLSPORTSAPI", "RSS_APP"}
    assert len(SOURCES) == len(names)
    for item in source_inventory():
        assert item["operational_status"] == "NOT_VERIFIED"
        assert item["model_feature_eligible"] is False
        assert item["model_rescore_verified"] is False
        assert item["prediction_authority"] is False
        assert item["can_execute"] is False


def test_newsapi_headlines_are_only_metadata():
    payload = {"status": "ok", "articles": [
        {"url": "https://example.org/story-1", "title": "Team injury report",
         "publishedAt": "2026-11-20T11:58:00Z"},
        {"url": "https://example.org/story-1", "title": "Repeated publisher story",
         "publishedAt": "2026-11-20T11:58:00Z"},
        {"url": "http://example.org/insecure", "title": "Insecure",
         "publishedAt": "2026-11-20T11:58:00Z"},
        {"url": "https://example.org/future", "title": "Future",
         "publishedAt": "2026-11-21T00:00:00Z"},
        {"url": "https://example.org/undated", "title": "Unknown", "publishedAt": None}
    ]}
    rows = normalize_newsapi_headlines(payload, observed_at=AT)
    assert len(rows) == 1
    row = rows[0]
    assert row["source_provider"] == "NEWSAPI"
    assert row["evidence_only"] is True
    assert row["publisher_claim_verified"] is False
    assert row["official_event_identity_verified"] is False
    assert row["historical_pregame_eligible"] is False
    receipt = source_receipt("NEWSAPI", rows, AT)
    assert receipt["source_status"] == "RESEARCH_METADATA_ONLY"
    assert receipt["article_count"] == 1
    assert receipt["model_rescore_verified"] is False
    assert receipt["can_execute"] is False


def test_newsapi_broken_shape_and_naive_time_fail_closed():
    assert normalize_newsapi_headlines([], observed_at=AT) == []
    assert normalize_newsapi_headlines({"status": "error"}, observed_at=AT) == []
    assert normalize_newsapi_headlines({"status": "ok", "articles": []},
                                      observed_at=AT.replace(tzinfo=None)) == []


def test_espn_rss_cannot_launder_external_links_or_future_claims():
    rss = b"""<?xml version="1.0"?>
    <rss version="2.0"><channel>
    <item><title>Starting QB update</title>
    <link>https://www.espn.com/nfl/story/_/id/1/story</link>
    <pubDate>Fri, 20 Nov 2026 11:40:00 GMT</pubDate></item>
    <item><title>Untrusted link</title>
    <link>https://example.com/fake</link>
    <pubDate>Fri, 20 Nov 2026 11:40:00 GMT</pubDate></item>
    <item><title>Postdated report</title>
    <link>https://www.espn.com/nba/story/_/id/2/story</link>
    <pubDate>Sat, 21 Nov 2026 11:40:00 GMT</pubDate></item>
    </channel></rss>"""
    rows = normalize_espn_rss(rss, observed_at=AT)
    assert len(rows) == 1
    assert rows[0]["source_provider"] == "ESPN_RSS"
    assert rows[0]["source_published_at_claim"].startswith("2026-11-20")
    assert rows[0]["model_feature_eligible"] is False


@pytest.mark.parametrize("raw", [
    b'<!DOCTYPE a [<!ENTITY x SYSTEM "file:///etc/passwd">]><rss/>',
    b"<rss><channel><item>",
    b"x" * 256001,
])
def test_espn_invalid_or_unsafe_xml_returns_no_articles(raw):
    assert normalize_espn_rss(raw, observed_at=AT) == []


def test_structured_sources_not_mislabeled_as_headline_publishers():
    with pytest.raises(ValueError, match="NEWS_SOURCE_NOT_EDITORIAL"):
        source_receipt("SPORTSDATAIO_GLOBAL", [], AT)
    with pytest.raises(ValueError, match="NEWS_SOURCE_TIMESTAMP_INVALID"):
        source_receipt("NEWSAPI", [], AT.replace(tzinfo=None))
