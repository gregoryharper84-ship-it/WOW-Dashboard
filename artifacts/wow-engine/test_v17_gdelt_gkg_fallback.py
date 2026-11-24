"""Offline contract tests for GDELT GKG dump fallback (#2749)."""
from __future__ import annotations

import io
import zipfile
from datetime import datetime, timedelta, timezone

import httpx

from v17 import gdelt_gkg_fallback as gkg
from v17 import news_feed_research_adapter as news
from v17 import public_gdelt_discovery as runner

NOW = datetime(2026, 11, 24, 16, 15, tzinfo=timezone.utc)


def _gkg_row(*, url: str, title: str, stamp: str = "20261124161500") -> str:
    extras = f"<PAGE_TITLE>{title}</PAGE_TITLE><PAGE_PRECISEPUBTIMESTAMP>{stamp}</PAGE_PRECISEPUBTIMESTAMP>"
    cols = [""] * 27
    cols[0] = f"{stamp}-1"
    cols[1] = stamp
    cols[2] = "1"
    cols[3] = "example.org"
    cols[4] = url
    cols[7] = "SPORT"
    cols[26] = extras
    return "\t".join(cols)


def _zip_bytes(csv_text: str) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("20261124161500.gkg.csv", csv_text)
    return buf.getvalue()


NBA_ROW = _gkg_row(
    url="https://www.espn.com/nba/story/_/id/1/spurs-wembanyama-out",
    title="Spurs rule Wembanyama out vs Kings",
)
NFL_NOISE = _gkg_row(
    url="https://www.example.org/world/politics/story",
    title="Diplomacy talks continue amid regional tension",
)
NHL_ROW = _gkg_row(
    url="https://www.tsn.ca/nhl/article/jets-vilardi",
    title="Winnipeg Jets' Gabriel Vilardi scores again",
)


def test_parse_gkg_keeps_sport_title_or_url_and_requires_page_title():
    csv = "\n".join([NBA_ROW, NFL_NOISE, NHL_ROW, "not-a-row"]) + "\n"
    articles = gkg.parse_gkg_articles(csv.encode(), sport_key="basketball_nba",
                                      observed_at=NOW)
    assert len(articles) == 1
    assert articles[0]["title"].startswith("Spurs rule")
    assert articles[0]["source_url"].startswith("https://www.espn.com/nba/")
    assert articles[0]["publisher_claim_verified"] is False
    assert articles[0]["historical_pregame_eligible"] is False


def test_parse_rejects_titleless_gkg_rows():
    cols = NBA_ROW.split("\t")
    cols[-1] = ""  # no PAGE_TITLE
    articles = gkg.parse_gkg_articles(("\t".join(cols) + "\n").encode(),
                                      sport_key="basketball_nba",
                                      observed_at=NOW)
    assert articles == []


def test_fetch_gkg_fallback_from_dump(monkeypatch):
    monkeypatch.setattr(gkg, "_now", lambda: NOW)
    zip_payload = _zip_bytes(NBA_ROW + "\n" + NHL_ROW + "\n")
    lastupdate = (
        "100 aaa http://data.gdeltproject.org/gdeltv2/20261124161500.export.CSV.zip\n"
        "200 bbb http://data.gdeltproject.org/gdeltv2/20261124161500.gkg.csv.zip\n"
    ).encode()

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        assert request.url.host == gkg.GDELT_DATA_HOST
        assert request.headers.get("Authorization") is None
        if path.endswith("lastupdate.txt"):
            return httpx.Response(200, headers={"Content-Type": "text/plain"},
                                  stream=httpx.ByteStream(lastupdate))
        if path.endswith(".gkg.csv.zip"):
            return httpx.Response(200, headers={"Content-Type": "application/zip"},
                                  stream=httpx.ByteStream(zip_payload))
        return httpx.Response(404, stream=httpx.ByteStream(b""))

    with httpx.Client(transport=httpx.MockTransport(handler),
                      follow_redirects=False) as client:
        result = gkg.fetch_gdelt_gkg_news("basketball_nba", client=client, at=NOW)
    assert result.ok and result.code == "SOURCE_FETCH_OK"
    assert result.data["acquisition_path"] == "GDELT_GKG_DUMP_FALLBACK"
    assert result.data["articles"][0]["title"].startswith("Spurs rule")
    assert result.can_execute is False and result.prediction_authority is False


def test_fetch_gkg_walks_back_when_listed_slice_404(monkeypatch):
    monkeypatch.setattr(gkg, "_now", lambda: NOW)
    zip_payload = _zip_bytes(NBA_ROW + "\n")
    # Stale pointer (same failure mode observed 2026-11-24).
    lastupdate = (
        "200 bbb http://data.gdeltproject.org/gdeltv2/20261124170000.gkg.csv.zip\n"
    ).encode()
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        seen.append(path)
        if path.endswith("lastupdate.txt"):
            return httpx.Response(200, stream=httpx.ByteStream(lastupdate))
        if path.endswith("20261124170000.gkg.csv.zip"):
            return httpx.Response(404, stream=httpx.ByteStream(b""))
        if path.endswith("20261124161500.gkg.csv.zip"):
            return httpx.Response(200, stream=httpx.ByteStream(zip_payload))
        return httpx.Response(404, stream=httpx.ByteStream(b""))

    with httpx.Client(transport=httpx.MockTransport(handler),
                      follow_redirects=False) as client:
        result = gkg.fetch_gdelt_gkg_news("basketball_nba", client=client, at=NOW)
    assert result.ok
    assert any(p.endswith("20261124170000.gkg.csv.zip") for p in seen)
    assert any(p.endswith("20261124161500.gkg.csv.zip") for p in seen)


def test_discover_uses_gkg_when_doc_rate_limited(monkeypatch):
    monkeypatch.setattr(news, "_now", lambda: NOW)
    monkeypatch.setattr(gkg, "_now", lambda: NOW)
    zip_payload = _zip_bytes(NHL_ROW + "\n")
    lastupdate = (
        "200 bbb http://data.gdeltproject.org/gdeltv2/20261124161500.gkg.csv.zip\n"
    ).encode()

    def handler(request: httpx.Request) -> httpx.Response:
        host = request.url.host
        if host == "api.gdeltproject.org":
            return httpx.Response(
                429, headers={"Content-Type": "text/plain"},
                stream=httpx.ByteStream(b"rate limited"))
        if host == gkg.GDELT_DATA_HOST:
            if request.url.path.endswith("lastupdate.txt"):
                return httpx.Response(200, stream=httpx.ByteStream(lastupdate))
            return httpx.Response(200, stream=httpx.ByteStream(zip_payload))
        raise AssertionError(f"unexpected host {host}")

    with httpx.Client(transport=httpx.MockTransport(handler),
                      follow_redirects=False) as client:
        receipt = runner.discover(NOW, sport="icehockey_nhl", client=client)
    assert receipt["source_status"] == "OK"
    assert receipt["source_code"] == "SOURCE_FETCH_OK"
    assert receipt["acquisition_path"] == "GDELT_GKG_DUMP_FALLBACK"
    assert receipt["fallback_status"] == "RECOVERED"
    assert receipt["attempt_count"] == 2
    assert receipt["articles"][0]["title"].startswith("Winnipeg Jets")
    assert receipt["retry_deferred"] is False
    assert receipt["can_execute"] is False


def test_discover_preserves_doc_failure_when_gkg_also_empty(monkeypatch):
    monkeypatch.setattr(news, "_now", lambda: NOW)
    monkeypatch.setattr(gkg, "_now", lambda: NOW)
    lastupdate = (
        "200 bbb http://data.gdeltproject.org/gdeltv2/20261124161500.gkg.csv.zip\n"
    ).encode()
    empty_zip = _zip_bytes(NFL_NOISE + "\n")

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "api.gdeltproject.org":
            return httpx.Response(429, stream=httpx.ByteStream(b"nope"))
        if request.url.path.endswith("lastupdate.txt"):
            return httpx.Response(200, stream=httpx.ByteStream(lastupdate))
        return httpx.Response(200, stream=httpx.ByteStream(empty_zip))

    with httpx.Client(transport=httpx.MockTransport(handler),
                      follow_redirects=False) as client:
        receipt = runner.discover(NOW, sport="basketball_nba", client=client)
    assert receipt["source_status"] == "BLOCKED"
    # DOC native failure remains the authoritative source_code; GKG outcome is
    # recorded separately so operators do not lose the 429/timeout evidence.
    assert receipt["source_code"] == "SOURCE_RATE_LIMITED"
    assert receipt["acquisition_path"] == "GDELT_DOC"
    assert receipt["fallback_status"] == "SOURCE_NO_VALID_NEWS"
    assert receipt["retry_deferred"] is True
    assert receipt["attempt_count"] == 2
    assert receipt["articles"] == []


def test_november_active_season_prioritizes_live_leagues():
    active = runner.active_sport_keys(NOW)
    assert "basketball_nba" in active
    assert "americanfootball_nfl" in active
    assert "icehockey_nhl" in active
    assert "baseball_mlb" not in active
    assert "basketball_wnba" not in active
    # Four daily slots over two days sample active sports, not the full 13.
    start = datetime(2026, 11, 24, 0, 30, tzinfo=timezone.utc)
    sampled = [runner.selection(start + timedelta(hours=6 * i))[0] for i in range(8)]
    assert set(sampled).issubset(set(active))
    assert "basketball_nba" in sampled or "americanfootball_nfl" in sampled


def test_all_inventory_sports_still_have_queries():
    assert len(runner.SPORT_QUERIES) == 13
    assert {news.normalize_sport(key) for key in runner.SPORT_QUERY_MAP} == set(
        news.WEIGHT_PROFILES)
    for month in range(1, 13):
        keys = runner.active_sport_keys(NOW.replace(month=month, day=15))
        assert keys
        assert set(keys).issubset(set(runner.SPORT_QUERY_MAP))
