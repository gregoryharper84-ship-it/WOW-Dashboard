"""PropsMadness NBA public-page discovery extractor: no actual provider I/O in tests."""
from __future__ import annotations

import json
import sys
from pathlib import Path
from urllib.error import HTTPError

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from v17.propsmadness_nba_public_explore import (
    PublicPageHold, parse_public_explore_html, fetch_public_explore_once,
)

PAGE = b"""<!DOCTYPE html><html><head><script>
  window.fake = {"player":999,"line":500,"model_probability":0.9};
</script></head><body>
<nav><a href="/nba/props/points">Points</a></nav>
<h1>NBA Points Props Today</h1><div>Updated 3:40 PM EDT</div>
<section>
<a href="/nba?market=player-points&amp;match=18594&amp;player=295">K. DurantKevin Durant</a>
<div>Points 23.5&#183; HOU @ SAS</div>
<div>1/1</div><div>O -120 U -110</div><div>L10 AVG</div><div>30.0</div>
<div>Matchup C</div><div>Opp DefRtg #14</div>
<a href="/nba?market=player-points&amp;match=18594&amp;player=297">A. SengunAlperen Sengun</a>
<div>Points 17.5&#183; HOU @ SAS</div><div>O -125 U -106</div>
<div>L10 AVG 23.0</div>
<a href="https://not-propsmadness.example/nba?market=player-points&amp;match=1&amp;player=1">Fake Offsite</a>
</section></body></html>"""


def parse(html=PAGE, market="points"):
    return parse_public_explore_html(html, market=market, fetched_at="2026-10-23T21:00:00Z")


def test_extracts_visible_player_prop_rows_without_adding_false_market_authority():
    r = parse()
    assert r["row_count"] == 2
    assert r["status"] == "PUBLIC_ROWS_PARSED_DISCOVERY_ONLY"
    one, two = r["rows"]
    assert (one["provider_player_id"], one["provider_match_id"]) == ("295", "18594")
    assert one["line"] == 23.5
    assert one["over_odds_display"] == -120
    assert one["under_odds_display"] == -110
    assert one["l10_avg_display"] == 30.0
    assert two["line"] == 17.5
    assert one["canonical_player_id"] is None
    assert one["canonical_event_id"] is None
    assert one["sportsbook"] is None
    assert one["event_start_utc"] is None
    assert one["historical_line_evidence"] is False
    assert one["pregame_verified"] is False
    assert one["exact_line_market_authority"] is False
    assert r["history_materialized"] is False
    assert r["historical_training_authorized"] is False
    assert r["probability_publishable"] is False
    assert r["can_execute"] is False
    assert "0.9" not in json.dumps(r)


def test_changing_stat_or_third_party_url_is_not_a_valid_prop():
    assert parse(PAGE.replace(b"Points 23.5", b"Rebounds 23.5"))["row_count"] == 1
    assert parse(PAGE.replace(b"market=player-points", b"market=player-rebounds"))["row_count"] == 0
    assert parse(PAGE.replace(b"https://not-propsmadness.example", b"https://evil.example"))["row_count"] == 2


def test_unknown_dom_schema_and_client_only_pages_fail_with_no_rows():
    r = parse(b"<html><body><div id='app'>Loading...</div></body></html>")
    assert r["status"] == "PUBLIC_HTML_NO_VERIFIED_ROWS"
    assert r["rows"] == []
    assert r["probability_publishable"] is False


def test_rejects_duplicate_player_game_line_and_untrusted_market():
    dup = b"""<html><body>
      <a href="/nba?market=player-points&amp;match=11&amp;player=22">P. A</a>
      <span>Points 11.5&#183; BOS vs NYK</span>
      <a href="/nba?market=player-points&amp;match=11&amp;player=22">P. A</a>
      <span>Points 11.5&#183; BOS vs NYK</span>
    </body></html>"""
    with pytest.raises(PublicPageHold, match="PM_PUBLIC_DUPLICATE_PLAYER_EVENT_LINE"):
        parse(dup)


def test_missing_capture_timezone_and_unsupported_market_rejected():
    with pytest.raises(PublicPageHold, match="PM_PUBLIC_CAPTURE_TIME_INVALID"):
        parse_public_explore_html(PAGE, market="points", fetched_at="2026-10-23T15:00:00")
    with pytest.raises(PublicPageHold, match="PM_PUBLIC_MARKET_NOT_ALLOWLISTED"):
        parse_public_explore_html(PAGE, market="prajunk", fetched_at="2026-10-23T15:00:00Z")


def test_no_live_fetch_without_explicit_personal_research_confirmation():
    with pytest.raises(PublicPageHold, match="PM_PUBLIC_USE_NOT_CONFIRMED"):
        fetch_public_explore_once("points", personal_noncommercial_use_confirmed=False)
    with pytest.raises(PublicPageHold, match="PM_PUBLIC_MARKET_NOT_ALLOWLISTED"):
        fetch_public_explore_once("fantasy", personal_noncommercial_use_confirmed=True)


class FakeResponse:
    status = 200
    def __init__(self, req, data):
        self.req = req
        self.data = data
    def __enter__(self): return self
    def __exit__(self, *args): pass
    def geturl(self): return self.req.full_url
    def read(self, n): return self.data[:n]


def opener_with(robots, html, visited):
    def fake_open(req, timeout):
        visited.append(req.full_url)
        return FakeResponse(req, robots if req.full_url.endswith("robots.txt") else html)
    return fake_open


def test_single_authorized_public_page_probe_only_reads_robots_and_market_html():
    visited = []
    r = fetch_public_explore_once(
        "points", personal_noncommercial_use_confirmed=True,
        urlopen=opener_with(b"User-agent: *\nAllow: /\n", PAGE, visited),
    )
    assert r["row_count"] == 2
    assert visited == [
        "https://propsmadness.com/robots.txt",
        "https://propsmadness.com/nba/props/points",
    ]


def test_robots_disallow_and_unavailable_policy_block_before_page_request():
    for robots, code in (
        (b"User-agent: *\nDisallow: /nba/props\n", "PM_PUBLIC_ROBOTS_DISALLOW"),
        (b"", "PM_PUBLIC_SOURCE_PAYLOAD_INVALID"),
        (b"unrecognized robots contents", "PM_PUBLIC_ROBOTS_UNVERIFIED"),
    ):
        visited = []
        with pytest.raises(PublicPageHold, match=code):
            fetch_public_explore_once(
                "points", personal_noncommercial_use_confirmed=True,
                urlopen=opener_with(robots, PAGE, visited),
            )
        assert visited == ["https://propsmadness.com/robots.txt"]


def test_live_robots_override_cannot_be_passed_without_mock():
    with pytest.raises(PublicPageHold, match="PM_PUBLIC_ROBOTS_OVERRIDE_FORBIDDEN"):
        fetch_public_explore_once(
            "points", personal_noncommercial_use_confirmed=True, robots_text="User-agent: *\nAllow: /",
        )


def test_http_403_and_429_are_terminating_not_retried():
    for code in (403, 429):
        visited = []
        def blocked(req, timeout):
            visited.append(req.full_url)
            raise HTTPError(req.full_url, code, "deny", {}, None)
        with pytest.raises(PublicPageHold, match="PM_PUBLIC_SOURCE_ACCESS_DENIED"):
            fetch_public_explore_once(
                "points", personal_noncommercial_use_confirmed=True,
                urlopen=blocked,
            )
        assert len(visited) == 1


def test_nonallowlisted_identity_anchor_does_not_grant_score():
    no_id = PAGE.replace(b"player=295", b"player=invalid")
    r = parse(no_id)
    assert r["row_count"] == 1
    assert not r["market_evidence_certified"]
