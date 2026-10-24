"""Multi-sport public-page parser / access-boundary tests, synthetic HTML only."""
from __future__ import annotations

import json
import sys
from pathlib import Path
from urllib.error import HTTPError

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from v17.propsmadness_multisport_public_explore import (
    MARKETS, parse_multisport_public_html, fetch_public_one_shot, PublicPageHold,
)

CASES = [
    ("NBA", "points", "player-points", "Points", "POINTS", "23.5", "BOS", "NYK"),
    ("NBA", "threes", "player-threes", "Threes", "THREE_POINTERS_MADE", "2.5", "CHI", "MIL"),
    ("NFL", "passing-yards", "player-passing-yards", "Passing Yards", "PASSING_YARDS", "226.5", "SEA", "CHI"),
    ("NFL", "receiving-yards", "player-receiving-yards", "Receiving Yards", "RECEIVING_YARDS", "55.5", "SF", "LA"),
    ("MLB", "strikeouts", "player-strikeouts", "Strikeouts", "PITCHER_STRIKEOUTS", "4.5", "NYY", "TB"),
    ("MLB", "pitcher-outs", "player-pitcher-outs", "Pitcher Outs", "PITCHING_OUTS", "17.5", "ATL", "NYM"),
    ("WNBA", "points", "player-points", "Points", "POINTS", "17.5", "NY", "LV"),
    ("WNBA", "rebounds", "player-rebounds", "Rebounds", "REBOUNDS", "8.5", "SEA", "IND"),
]


def page(sport, provider_market, label, line, team, opponent, *, player=295, event=18594):
    return (f"""<!doctype html><html><body><script>
      const hidden = "Model probability 90 percent";</script>
      <a href="/{sport.lower()}?market={provider_market}&amp;match={event}&amp;player={player}">
      A. SampleAlex Sample</a>
      <span>{label} {line}· {team} @ {opponent}</span>
      <span>O -114 U -120</span><div>L10 AVG</div><div>22.1</div>
    </body></html>""").encode()


@pytest.mark.parametrize("sport,market,code,label,stat,line,team,opp", CASES)
def test_each_supported_league_has_precise_market_record_and_no_authority(
    sport, market, code, label, stat, line, team, opp
):
    raw = page(sport, code, label, line, team, opp)
    result = parse_multisport_public_html(
        raw, sport=sport, market=market, fetched_at="2026-10-23T18:30:00Z"
    )
    assert result["status"] == "PUBLIC_ROWS_PARSED_DISCOVERY_ONLY"
    assert result["row_count"] == 1
    assert result["sport"] == sport
    item = result["rows"][0]
    assert item["stat_type"] == stat and item["period"] == "FULL_GAME"
    assert item["display_line"] == float(line)
    assert item["provider_player_id"] == "295"
    assert item["provider_match_id"] == "18594"
    assert item["over_odds_display"] == -114 and item["under_odds_display"] == -120
    assert item["l10_avg_display"] == 22.1
    assert result["source_url"] == f"https://propsmadness.com/{sport.lower()}/props/{market}"
    assert item["sportsbook"] is None and item["canonical_event_id"] is None
    assert item["canonical_player_id"] is None and item["event_start_utc"] is None
    for label in ("pregame_verified", "historical_line_evidence", "exact_line_market_authority", "can_execute"):
        assert item[label] is False
    for field in ("historical_dataset_materialized", "historical_training_authorized",
                  "market_evidence_certified", "prediction_authority",
                  "probability_publishable", "can_execute"):
        assert result[field] is False
    assert "90 percent" not in json.dumps(result)


def test_16_scoped_markets_across_exactly_four_sports():
    assert set(MARKETS) == {"NBA", "NFL", "MLB", "WNBA"}
    assert all(len(markets) == 4 for markets in MARKETS.values())
    assert sum(map(len, MARKETS.values())) == 16


def test_mixed_sport_market_and_external_player_href_never_join():
    mlb = page("MLB", "player-strikeouts", "Strikeouts", "4.5", "NYY", "TB")
    assert parse_multisport_public_html(mlb, sport="NFL", market="passing-yards", fetched_at="2026-10-23T12:00:00Z")["row_count"] == 0
    evil = mlb.replace(b"/mlb?market=", b"https://other-site.example/mlb?market=")
    assert parse_multisport_public_html(evil, sport="MLB", market="strikeouts", fetched_at="2026-10-23T12:00:00Z")["row_count"] == 0


def test_sport_and_market_allowlist_and_timezone_fail_closed():
    fixture = page("NFL", "player-passing-yards", "Passing Yards", "200.5", "PIT", "BAL")
    for sport, market, code in (("NHL", "points", "PM_PUBLIC_SPORT_NOT_ALLOWLISTED"),
                                ("NFL", "points", "PM_PUBLIC_MARKET_NOT_ALLOWLISTED"),
                                ("mlb", "strikeouts", "PM_PUBLIC_SPORT_NOT_ALLOWLISTED")):
        with pytest.raises(PublicPageHold, match=code):
            parse_multisport_public_html(fixture, sport=sport, market=market, fetched_at="2026-10-23T20:00:00Z")
    with pytest.raises(PublicPageHold, match="PM_PUBLIC_CAPTURE_TIME_INVALID"):
        parse_multisport_public_html(fixture, sport="NFL", market="passing-yards", fetched_at="2026-10-23T20:00:00")


def test_locked_rows_without_lines_remain_missing_not_fabricated():
    locked = b"""<html><body><a href="/mlb?market=player-strikeouts&amp;match=21&amp;player=10">C. Pitcher</a>
    <span>Strikeouts&#183; NYY @ BOS</span><span>UNLOCK</span></body></html>"""
    r = parse_multisport_public_html(locked, sport="MLB", market="strikeouts", fetched_at="2026-10-23T20:00:00Z")
    assert r["status"] == "PUBLIC_HTML_NO_VERIFIED_ROWS"
    assert r["row_count"] == 0 and r["rows"] == []
    assert not r["historical_dataset_materialized"]


def test_duplicate_player_event_line_not_silently_collapsed():
    one = page("NFL", "player-passing-yards", "Passing Yards", "226.5", "SEA", "CHI")
    combined = one.replace(b"</body>", b"""<a href="/nfl?market=player-passing-yards&amp;match=18594&amp;player=295">Same</a>
    <span>Passing Yards 226.5&#183; SEA @ CHI</span></body>""")
    with pytest.raises(PublicPageHold, match="PM_PUBLIC_DUPLICATE_PLAYER_EVENT_LINE"):
        parse_multisport_public_html(combined, sport="NFL", market="passing-yards", fetched_at="2026-10-23T20:00:00Z")


class Response:
    status = 200
    def __init__(self, req, body):
        self.req, self.body = req, body
    def __enter__(self): return self
    def __exit__(self, *args): pass
    def geturl(self): return self.req.full_url
    def read(self, n): return self.body[:n]


def stub(robots: bytes, page_html: bytes, urls: list[str]):
    def fetch(req, timeout):
        urls.append(req.full_url)
        return Response(req, robots if req.full_url.endswith("robots.txt") else page_html)
    return fetch


def test_only_two_requests_and_exact_one_sport_market():
    urls = []
    result = fetch_public_one_shot(
        "NFL", "passing-yards", personal_noncommercial_use_confirmed=True,
        urlopen=stub(b"User-agent: *\nAllow: /\n",
                     page("NFL", "player-passing-yards", "Passing Yards", "226.5", "SEA", "CHI"), urls),
    )
    assert result["row_count"] == 1
    assert urls == ["https://propsmadness.com/robots.txt", "https://propsmadness.com/nfl/props/passing-yards"]


def test_robots_unknown_and_disallowed_stop_before_source_request():
    for txt, expected in ((b"not a robots file", "PM_PUBLIC_ROBOTS_UNVERIFIED"),
                          (b"User-agent: *\nDisallow: /wnba/props\n", "PM_PUBLIC_ROBOTS_DISALLOW")):
        urls = []
        with pytest.raises(PublicPageHold, match=expected):
            fetch_public_one_shot(
                "WNBA", "points", personal_noncommercial_use_confirmed=True,
                urlopen=stub(txt, b"<html></html>", urls),
            )
        assert urls == ["https://propsmadness.com/robots.txt"]


def test_no_user_consent_is_zero_request_and_http_429_stops_without_retry():
    urls = []
    with pytest.raises(PublicPageHold, match="PM_PUBLIC_USE_NOT_CONFIRMED"):
        fetch_public_one_shot("MLB", "strikeouts", personal_noncommercial_use_confirmed=False,
                              urlopen=stub(b"User-agent: *\nAllow: /\n", b"<html/>", urls))
    assert urls == []
    def denied(req, timeout):
        urls.append(req.full_url)
        raise HTTPError(req.full_url, 429, "Too Many Requests", {}, None)
    with pytest.raises(PublicPageHold, match="PM_PUBLIC_SOURCE_ACCESS_DENIED"):
        fetch_public_one_shot("MLB", "strikeouts", personal_noncommercial_use_confirmed=True, urlopen=denied)
    assert urls == ["https://propsmadness.com/robots.txt"]
