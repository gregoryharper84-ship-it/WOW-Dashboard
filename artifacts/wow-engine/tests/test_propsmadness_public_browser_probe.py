"""Browser-rendered public page probe boundary; no live website connections."""
from __future__ import annotations

import sys
from pathlib import Path
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from v17 import propsmadness_public_browser_probe as probe
from v17.propsmadness_multisport_public_explore import PublicPageHold


def test_only_supported_robots_rule_allows_public_path():
    probe._robots_permits(
        b"User-agent: *\nAllow: /\n",
        "https://propsmadness.com/nfl/props/passing-yards",
    )
    with pytest.raises(PublicPageHold, match="PM_PUBLIC_ROBOTS_DISALLOW"):
        probe._robots_permits(
            b"User-agent: *\nDisallow: /mlb/props/\n",
            "https://propsmadness.com/mlb/props/strikeouts",
        )
    with pytest.raises(PublicPageHold, match="PM_PUBLIC_ROBOTS_UNVERIFIED"):
        probe._robots_permits(b"garbled robots", "https://propsmadness.com/nba/props/points")


def test_no_consent_no_http_or_browser_calls(monkeypatch):
    def should_never_call(*_args):
        raise AssertionError("unexpected external I/O")
    monkeypatch.setattr(probe, "_check_robots_live", should_never_call)
    monkeypatch.setattr(probe, "_render_public_page", should_never_call)
    with pytest.raises(PublicPageHold, match="PM_PUBLIC_USE_NOT_CONFIRMED"):
        probe.probe_public_rendered_once("NBA", "points",
                                         personal_noncommercial_use_confirmed=False)


def test_allowed_url_and_robot_checks_precede_browser(monkeypatch):
    trace = []
    def robots(url):
        trace.append(("robots", url))
    def browser(url):
        trace.append(("render", url))
        return (b'<html><body><a href="/wnba?market=player-points&amp;match=20&amp;player=11">'
                b'A. Test</a><span>Points 12.5&#183; NY @ LV</span></body></html>')
    monkeypatch.setattr(probe, "_check_robots_live", robots)
    monkeypatch.setattr(probe, "_render_public_page", browser)
    result = probe.probe_public_rendered_once(
        "WNBA", "points", personal_noncommercial_use_confirmed=True,
    )
    assert result["row_count"] == 1
    assert result["sport"] == "WNBA"
    assert result["historical_training_authorized"] is False
    assert result["probability_publishable"] is False
    assert result["can_execute"] is False
    url = "https://propsmadness.com/wnba/props/points"
    assert trace == [("robots", url), ("render", url)]


def test_browser_never_runs_after_robot_denial(monkeypatch):
    def denied(_url):
        raise PublicPageHold("PM_PUBLIC_ROBOTS_DISALLOW")
    def unexpected(_url):
        raise AssertionError("browser called after denied robots policy")
    monkeypatch.setattr(probe, "_check_robots_live", denied)
    monkeypatch.setattr(probe, "_render_public_page", unexpected)
    with pytest.raises(PublicPageHold, match="PM_PUBLIC_ROBOTS_DISALLOW"):
        probe.probe_public_rendered_once("NFL", "passing-yards",
                                         personal_noncommercial_use_confirmed=True)


def test_unknown_markets_fail_before_http(monkeypatch):
    monkeypatch.setattr(probe, "_check_robots_live", lambda _url: (_ for _ in ()).throw(AssertionError("I/O")))
    with pytest.raises(PublicPageHold, match="PM_PUBLIC_MARKET_NOT_ALLOWLISTED"):
        probe.probe_public_rendered_once("MLB", "nonsense",
                                         personal_noncommercial_use_confirmed=True)
