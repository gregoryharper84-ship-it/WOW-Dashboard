"""Regression coverage for LLP TheRundown opening/current/closing evidence.

These tests assert that richer market context stays evidence-only and never
mutates fitted LLP sporting probability, calibrated bounds, or can_execute.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from v17 import llp_rundown_market_bridge as bridge
from v17 import market_evidence_native_live as live
from v17 import market_evidence_sources as sources


class Request:
    def __init__(self, *, intent: str = "WINNER"):
        self.requested_slate_date = "2026-09-16"
        self.sport = "MLB"
        self.league = "MLB"
        self.home_team = "Chicago Cubs"
        self.away_team = "Milwaukee Brewers"
        self.official_event_id = "evt-1"
        self.decision_intent = intent
        self.market_prior = None


def _event(*, phase: str) -> dict:
    if phase == "OPENING":
        a_home, a_away, a_total = -120, 105, 8.0
        b_home, b_away, b_total = -118, 102, 8.5
        stamp_a, stamp_b = "2026-09-15T14:00:00Z", "2026-09-15T14:01:00Z"
    elif phase == "CLOSING":
        a_home, a_away, a_total = -145, 125, 9.0
        b_home, b_away, b_total = -142, 122, 9.0
        stamp_a, stamp_b = "2026-09-16T23:55:00Z", "2026-09-16T23:56:00Z"
    else:
        a_home, a_away, a_total = -135, 115, 8.5
        b_home, b_away, b_total = -130, 110, 9.0
        stamp_a, stamp_b = "2026-09-16T18:00:00Z", "2026-09-16T18:01:00Z"

    def book(key: str, title: str, home: int, away: int, total: float, stamp: str) -> dict:
        return {
            "key": key,
            "title": title,
            "last_update": stamp,
            "markets": [
                {
                    "key": "h2h",
                    "last_update": stamp,
                    "outcomes": [
                        {"name": "Chicago Cubs", "price": home},
                        {"name": "Milwaukee Brewers", "price": away},
                    ],
                },
                {
                    "key": "totals",
                    "last_update": stamp,
                    "outcomes": [
                        {"name": "Over", "price": -110, "point": total},
                        {"name": "Under", "price": -110, "point": total},
                    ],
                },
            ],
        }

    return {
        "id": "rundown-evt-1",
        "home_team": "Chicago Cubs",
        "away_team": "Milwaukee Brewers",
        "bookmakers": [
            book("book-a", "Book A", a_home, a_away, a_total, stamp_a),
            book("book-b", "Book B", b_home, b_away, b_total, stamp_b),
        ],
    }


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.setattr(sources, "ENABLED", True)
    monkeypatch.setenv("WOW_LLP_RUNDOWN_MARKET_ENABLED", "true")
    monkeypatch.delenv("WOW_RUNDOWN_LLP_AFFILIATE_IDS", raising=False)


def _result(capability: str) -> sources.MarketEvidenceResult:
    phase = "OPENING" if capability == "openers" else "CURRENT"
    return sources.MarketEvidenceResult(
        True,
        "RUNDOWN",
        capability,
        data=[_event(phase=phase)],
        code="MARKET_EVIDENCE_NORMALISED",
    )


def test_llp_context_contains_opening_current_ml_total_and_clv_compat(monkeypatch):
    calls: list[dict] = []

    def snapshot(*args, **kwargs):
        calls.append(dict(kwargs))
        return _result(str(kwargs["capability"]))

    monkeypatch.setattr(live, "get_sport_date_odds_snapshot", snapshot)

    context = bridge.resolve_rundown_market_context(Request())

    assert context["status"] == "EXACT_LINE"
    assert context["market_ids"] == ["1", "2", "3"]
    assert context["current"]["moneyline"]["home_best"]["american_odds"] == -130.0
    assert context["current"]["moneyline"]["away_best"]["american_odds"] == 115.0
    assert context["current"]["game_total"]["consensus_main_line"] == pytest.approx(8.75)
    assert context["current"]["game_total"]["lowest_over_line"]["over_point"] == 8.5
    assert context["current"]["game_total"]["highest_under_line"]["under_point"] == 9.0

    assert context["opening"]["moneyline"]["home_best"]["american_odds"] == -118.0
    assert context["opening"]["game_total"]["consensus_main_line"] == pytest.approx(8.25)

    clv = context["clv_market_evidence"]
    assert clv["open_odds"] == {"home": -118.0, "away": 105.0}
    assert clv["current_odds"] == {"home": -130.0, "away": 115.0}
    assert clv["close_odds"] is None
    assert clv["closing_market_probability"] is None
    assert clv["closing_status"] == "PENDING_NATIVE_CLOSING_SNAPSHOT"

    compat = context["prop_tagger_compat"]
    assert compat["source"] == "odds_api"
    assert compat["provider"] == "RUNDOWN"
    assert compat["gameTotal"] == pytest.approx(8.75)
    assert compat["bookmakerOdds"] == {"home": -130.0, "away": 115.0}
    assert compat["openLine"] == {"home": -118.0, "away": 105.0}
    assert compat["controlling_route"] == "LLP_TEAM_BETTING_ENGINE"
    assert compat["prediction_authority"] is False
    assert compat["can_execute"] is False

    current_call = next(call for call in calls if call["capability"] == "events")
    opener_call = next(call for call in calls if call["capability"] == "openers")
    assert tuple(current_call["market_ids"]) == ("1", "2", "3")
    assert current_call["main_line"] is True
    assert current_call["hide_closed"] is True
    assert tuple(opener_call["market_ids"]) == ("1", "2", "3")
    assert opener_call["main_line"] is None
    assert opener_call["hide_closed"] is None


def test_opener_failure_does_not_erase_current_llp_probability_context(monkeypatch):
    def snapshot(*args, **kwargs):
        if kwargs["capability"] == "openers":
            return sources.MarketEvidenceResult(
                False,
                "RUNDOWN",
                "openers",
                code="RUNDOWN_HTTP_403",
                status=403,
            )
        return _result("events")

    monkeypatch.setattr(live, "get_sport_date_odds_snapshot", snapshot)

    context = bridge.resolve_rundown_market_context(Request())
    assert context["status"] == "EXACT_LINE"
    assert context["home_probability"] + context["away_probability"] == pytest.approx(1.0)
    assert context["opening"]["status"] == "MARKET_DATA_UNOBTAINABLE"
    assert context["opening"]["reason_code"] == "RUNDOWN_HTTP_403"
    assert context["clv_market_evidence"]["open_odds"] == {"home": None, "away": None}
    assert context["prediction_authority"] is False


def test_native_closing_context_is_distinct_from_current_snapshot(monkeypatch):
    monkeypatch.setattr(
        bridge,
        "_closing_snapshot",
        lambda *args, **kwargs: sources.MarketEvidenceResult(
            True,
            "RUNDOWN",
            "closing",
            data=[_event(phase="CLOSING")],
            code="MARKET_EVIDENCE_NORMALISED",
        ),
    )

    close = bridge.resolve_rundown_closing_context(Request())
    assert close["status"] == "EXACT_CLOSING_LINE"
    assert close["close_odds"] == {"home": -142.0, "away": 125.0}
    assert close["closing_market_probability"]["home"] + close["closing_market_probability"]["away"] == pytest.approx(1.0)
    assert close["closing"]["game_total"]["consensus_main_line"] == 9.0
    assert close["probability_mutated_by_bridge"] is False
    assert close["can_execute"] is False


def test_llp_fitted_probability_is_unchanged_by_rich_market_evidence(monkeypatch):
    monkeypatch.setattr(
        live,
        "get_sport_date_odds_snapshot",
        lambda *args, **kwargs: _result(str(kwargs["capability"])),
    )
    seen: dict = {}

    def score(req, *, event_api, canonical_hydration_required=False):
        seen["market_prior"] = dict(req.market_prior or {})
        return {
            "probability_publishable": True,
            "rank_eligible": True,
            "calibrated_home_probability": 0.681,
            "calibrated_away_probability": 0.319,
            "calibrated_home_lower_bound": 0.641,
            "can_execute": False,
        }

    module = SimpleNamespace(score_team_event_request=score)
    assert bridge.install_llp_rundown_market_bridge(module) is True
    result = module.score_team_event_request(Request(), event_api=object())

    assert seen["market_prior"]["source"] == bridge.BRIDGE_SOURCE
    assert result["calibrated_home_probability"] == pytest.approx(0.681)
    assert result["calibrated_home_lower_bound"] == pytest.approx(0.641)
    evidence = result["llp_rundown_market_evidence"]
    assert evidence["probability_mutated_by_bridge"] is False
    assert evidence["probability_mutated_by_evidence"] is False
    assert evidence["current"]["game_total"]["consensus_main_line"] == pytest.approx(8.75)
    assert result["can_execute"] is False
