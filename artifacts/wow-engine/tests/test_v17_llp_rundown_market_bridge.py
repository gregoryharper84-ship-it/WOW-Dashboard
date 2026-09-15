from __future__ import annotations

from types import SimpleNamespace

import pytest

from v17 import llp_rundown_market_bridge as bridge
from v17 import market_evidence_native_live as live
from v17 import market_evidence_sources as sources


class Request:
    def __init__(self, *, intent="WINNER", prior=None, sport="MLB", league="MLB"):
        self.requested_slate_date = "2026-09-14"
        self.sport = sport
        self.league = league
        self.home_team = "Chicago Cubs"
        self.away_team = "Milwaukee Brewers"
        self.official_event_id = "evt-1"
        self.decision_intent = intent
        self.market_prior = prior


def _event(*, draw=False):
    outcomes = [
        {"name": "Chicago Cubs", "price": -135},
        {"name": "Milwaukee Brewers", "price": 115},
    ]
    if draw:
        outcomes.append({"name": "Draw", "price": 240})
    return {
        "id": "rundown-evt-1",
        "home_team": "Chicago Cubs",
        "away_team": "Milwaukee Brewers",
        "bookmakers": [
            {
                "key": "book-a",
                "title": "Book A",
                "last_update": "2026-09-14T18:00:00Z",
                "markets": [{"key": "h2h", "outcomes": outcomes}],
            },
            {
                "key": "book-b",
                "title": "Book B",
                "last_update": "2026-09-14T18:01:00Z",
                "markets": [{
                    "key": "h2h",
                    "outcomes": [
                        {"name": "Chicago Cubs", "price": -130},
                        {"name": "Milwaukee Brewers", "price": 110},
                    ],
                }],
            },
        ],
    }


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.setattr(sources, "ENABLED", True)
    monkeypatch.setenv("WOW_LLP_RUNDOWN_MARKET_ENABLED", "true")


def test_resolve_builds_cross_book_no_vig_context(monkeypatch):
    monkeypatch.setattr(
        live,
        "get_sport_date_odds_snapshot",
        lambda *a, **k: sources.MarketEvidenceResult(
            True, "RUNDOWN", "events", data=[_event()], code="MARKET_EVIDENCE_FETCH_OK"
        ),
    )
    context = bridge.resolve_rundown_market_context(Request())
    assert context["status"] == "EXACT_LINE"
    assert context["source"] == "RUNDOWN_MARKET_EVIDENCE"
    assert context["book_count"] == 2
    assert context["home_probability"] + context["away_probability"] == pytest.approx(1.0)
    assert context["favorite"] == "Chicago Cubs"
    assert context["prediction_authority"] is False
    assert context["can_execute"] is False


def test_bridge_injects_market_context_before_llp_score(monkeypatch):
    monkeypatch.setattr(
        bridge,
        "resolve_rundown_market_context",
        lambda req: {
            "status": "EXACT_LINE",
            "provider": bridge.BRIDGE_SOURCE,
            "snapshot_id": "rundown:1:t",
            "timestamp": "2026-09-14T18:00:00Z",
            "home_probability": 0.62,
            "away_probability": 0.38,
            "quality": "CROSS_BOOK_NO_VIG",
            "source": bridge.BRIDGE_SOURCE,
            "book_count": 3,
            "favorite": "Chicago Cubs",
            "prediction_authority": False,
            "can_execute": False,
        },
    )
    seen = {}

    def score(req, *, event_api, canonical_hydration_required=False):
        seen["prior"] = dict(req.market_prior)
        return {"probability_publishable": True, "rank_eligible": True, "calibrated_home_probability": 0.66}

    module = SimpleNamespace(score_team_event_request=score)
    assert bridge.install_llp_rundown_market_bridge(module) is True
    req = Request()
    result = module.score_team_event_request(req, event_api=object())
    assert seen["prior"]["source"] == bridge.BRIDGE_SOURCE
    assert seen["prior"]["home_probability"] == pytest.approx(0.62)
    assert req.market_prior["snapshot_id"] == "rundown:1:t"
    assert result["probability_publishable"] is True
    assert result["rank_eligible"] is True
    assert result["llp_rundown_market_evidence"]["probability_mutated_by_bridge"] is False
    assert result["can_execute"] is False


def test_provider_failure_never_rewrites_model_status(monkeypatch):
    monkeypatch.setattr(
        bridge,
        "resolve_rundown_market_context",
        lambda req: {
            "status": "MARKET_DATA_UNOBTAINABLE",
            "provider": bridge.BRIDGE_SOURCE,
            "reason_code": "RUNDOWN_HTTP_503",
            "prediction_authority": False,
            "can_execute": False,
        },
    )

    def score(req, *, event_api, canonical_hydration_required=False):
        return {
            "code": "MODEL_SCORER_FAILED",
            "probability_publishable": False,
            "rank_eligible": False,
            "can_execute": False,
        }

    module = SimpleNamespace(score_team_event_request=score)
    bridge.install_llp_rundown_market_bridge(module)
    result = module.score_team_event_request(Request(), event_api=object())
    assert result["code"] == "MODEL_SCORER_FAILED"
    assert result["llp_rundown_market_evidence"]["reason_code"] == "RUNDOWN_HTTP_503"
    assert "MODEL_UNAVAILABLE" not in str(result)


def test_favorite_conflict_blocks_market_relative_rank_not_probability(monkeypatch):
    monkeypatch.setattr(
        bridge,
        "resolve_rundown_market_context",
        lambda req: {
            "status": "EXACT_LINE",
            "provider": bridge.BRIDGE_SOURCE,
            "snapshot_id": "rundown:1:t",
            "timestamp": "2026-09-14T18:00:00Z",
            "home_probability": 0.65,
            "away_probability": 0.35,
            "quality": "CROSS_BOOK_NO_VIG",
            "source": bridge.BRIDGE_SOURCE,
            "book_count": 3,
            "favorite": "Chicago Cubs",
            "prediction_authority": False,
            "can_execute": False,
        },
    )

    caller_prior = {
        "home_probability": 0.40,
        "away_probability": 0.60,
        "source": "CALLER_BOOK",
        "timestamp": "2026-09-14T17:59:00Z",
        "snapshot_id": "caller-1",
    }

    def score(req, *, event_api, canonical_hydration_required=False):
        return {
            "probability_publishable": True,
            "rank_eligible": True,
            "calibrated_home_probability": 0.70,
            "blockers": [],
        }

    module = SimpleNamespace(score_team_event_request=score)
    bridge.install_llp_rundown_market_bridge(module)
    result = module.score_team_event_request(Request(intent="UPSET", prior=caller_prior), event_api=object())
    assert result["probability_publishable"] is True
    assert result["rank_eligible"] is False
    assert "FAVORITE_STATUS_CONFLICT" in result["blockers"]
    assert result["market_role_status"] == "SOURCE_CONFLICT"
    assert result["llp_rundown_market_evidence"]["favorite_status_conflict"] is True


def test_winner_intent_preserves_rank_on_market_role_disagreement(monkeypatch):
    monkeypatch.setattr(
        bridge,
        "resolve_rundown_market_context",
        lambda req: {
            "status": "EXACT_LINE",
            "provider": bridge.BRIDGE_SOURCE,
            "snapshot_id": "rundown:1:t",
            "timestamp": "2026-09-14T18:00:00Z",
            "home_probability": 0.65,
            "away_probability": 0.35,
            "quality": "CROSS_BOOK_NO_VIG",
            "source": bridge.BRIDGE_SOURCE,
            "book_count": 2,
            "favorite": "Chicago Cubs",
            "prediction_authority": False,
            "can_execute": False,
        },
    )
    prior = {"home_probability": 0.4, "away_probability": 0.6, "source": "OTHER"}

    def score(req, *, event_api, canonical_hydration_required=False):
        return {"probability_publishable": True, "rank_eligible": True, "blockers": []}

    module = SimpleNamespace(score_team_event_request=score)
    bridge.install_llp_rundown_market_bridge(module)
    result = module.score_team_event_request(Request(intent="WINNER", prior=prior), event_api=object())
    assert result["probability_publishable"] is True
    assert result["rank_eligible"] is True
    assert result["llp_rundown_market_evidence"]["favorite_status_conflict"] is True


def test_three_way_market_is_not_collapsed_into_binary_prior(monkeypatch):
    monkeypatch.setattr(
        live,
        "get_sport_date_odds_snapshot",
        lambda *a, **k: sources.MarketEvidenceResult(
            True, "RUNDOWN", "events", data=[_event(draw=True)], code="MARKET_EVIDENCE_FETCH_OK"
        ),
    )
    req = Request(sport="SOCCER", league="EPL")
    context = bridge.resolve_rundown_market_context(req)
    assert context["status"] == "THREE_WAY_MARKET_UNSUPPORTED_FOR_BINARY_PRIOR"
    assert "home_probability" not in context
    assert context["prediction_authority"] is False


def test_installer_is_idempotent(monkeypatch):
    monkeypatch.setattr(bridge, "resolve_rundown_market_context", lambda req: {"status": "DISABLED", "provider": bridge.BRIDGE_SOURCE})

    def score(req, *, event_api, canonical_hydration_required=False):
        return {"can_execute": False}

    module = SimpleNamespace(score_team_event_request=score)
    assert bridge.install_llp_rundown_market_bridge(module) is True
    wrapped = module.score_team_event_request
    assert bridge.install_llp_rundown_market_bridge(module) is True
    assert module.score_team_event_request is wrapped
