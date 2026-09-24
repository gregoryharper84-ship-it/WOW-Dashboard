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


class TypedModelRequest:
    def __init__(self, market_prior):
        self.market_prior = market_prior

    def model_copy(self, *, update):
        return TypedModelRequest(update.get("market_prior", self.market_prior))


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


def _exact_context(*, home=0.62, away=0.38, favorite="Chicago Cubs"):
    return {
        "status": "EXACT_LINE",
        "provider": bridge.BRIDGE_SOURCE,
        "snapshot_id": "rundown:1:t",
        "timestamp": "2026-09-14T18:00:00Z",
        "home_probability": home,
        "away_probability": away,
        "quality": "CROSS_BOOK_NO_VIG",
        "source": bridge.BRIDGE_SOURCE,
        "book_count": 3,
        "favorite": favorite,
        "prediction_authority": False,
        "can_execute": False,
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


def test_generic_bridge_resolves_market_only_after_scorer_returns(monkeypatch):
    order = []

    def resolve(req):
        order.append("market")
        return _exact_context()

    monkeypatch.setattr(bridge, "resolve_rundown_market_context", resolve)

    def score(req, *, event_api, canonical_hydration_required=False):
        order.append("score")
        assert req.market_prior is None
        return {
            "probability_publishable": True,
            "rank_eligible": True,
            "calibrated_home_probability": 0.66,
        }

    module = SimpleNamespace(score_team_event_request=score)
    assert bridge.install_llp_rundown_market_bridge(module) is True
    req = Request()
    result = module.score_team_event_request(req, event_api=object())
    assert order == ["score", "market"]
    assert req.market_prior["snapshot_id"] == "rundown:1:t"
    assert result["probability_publishable"] is True
    assert result["rank_eligible"] is True
    evidence = result["llp_rundown_market_evidence"]
    assert evidence["market_context_timing"] == bridge.POST_SCORE_TIMING
    assert evidence["probability_mutated_by_bridge"] is False
    assert result["can_execute"] is False


def test_production_seams_strip_model_prior_then_resolve_market_before_envelope(monkeypatch):
    order = []
    seen = {}

    caller_prior = {
        "home_probability": 0.40,
        "away_probability": 0.60,
        "source": "CALLER_BOOK",
        "timestamp": "2026-09-14T17:59:00Z",
        "snapshot_id": "caller-1",
    }

    def resolve(req):
        order.append("market")
        return _exact_context(home=0.65, away=0.35)

    monkeypatch.setattr(bridge, "resolve_rundown_market_context", resolve)

    def mlb_request(req, event_api):
        return TypedModelRequest(req.market_prior)

    def build_envelope(req, *args, **kwargs):
        seen["envelope_market_data"] = kwargs.get("market_data")
        return {"market_data": kwargs.get("market_data")}

    module = SimpleNamespace(_mlb_request=mlb_request, _build_team_event_envelope=build_envelope)

    def score(req, *, event_api, canonical_hydration_required=False):
        typed = module._mlb_request(req, event_api)
        seen["model_market_prior"] = typed.market_prior
        order.append("score")
        envelope = module._build_team_event_envelope(req)
        assert envelope["market_data"]["status"] == "EXACT_LINE"
        return {
            "probability_publishable": True,
            "rank_eligible": True,
            "calibrated_home_probability": 0.70,
            "blockers": [],
        }

    module.score_team_event_request = score
    assert bridge.install_llp_rundown_market_bridge(module) is True
    req = Request(intent="UPSET", prior=caller_prior)
    result = module.score_team_event_request(req, event_api=object())

    assert order == ["score", "market"]
    assert seen["model_market_prior"] is None
    assert seen["envelope_market_data"]["source"] == bridge.BRIDGE_SOURCE
    assert seen["envelope_market_data"]["prior_probability"] == pytest.approx(0.65)
    assert req.market_prior["source"] == bridge.BRIDGE_SOURCE
    assert result["llp_rundown_market_evidence"]["model_market_prior_stripped"] is True
    assert result["llp_rundown_market_evidence"]["market_context_timing"] == bridge.POST_SCORE_TIMING
    assert result["llp_rundown_market_evidence"]["probability_mutated_by_bridge"] is False
    # The contradictory caller favorite is a downstream rank conflict only.
    assert result["calibrated_home_probability"] == pytest.approx(0.70)
    assert result["rank_eligible"] is False
    assert "FAVORITE_STATUS_CONFLICT" in result["blockers"]


def test_provider_failure_never_rewrites_model_status_and_occurs_after_score(monkeypatch):
    order = []

    def resolve(req):
        order.append("market")
        return {
            "status": "MARKET_DATA_UNOBTAINABLE",
            "provider": bridge.BRIDGE_SOURCE,
            "reason_code": "RUNDOWN_HTTP_503",
            "prediction_authority": False,
            "can_execute": False,
        }

    monkeypatch.setattr(bridge, "resolve_rundown_market_context", resolve)

    def score(req, *, event_api, canonical_hydration_required=False):
        order.append("score")
        return {
            "code": "MODEL_SCORER_FAILED",
            "probability_publishable": False,
            "rank_eligible": False,
            "can_execute": False,
        }

    module = SimpleNamespace(score_team_event_request=score)
    bridge.install_llp_rundown_market_bridge(module)
    result = module.score_team_event_request(Request(), event_api=object())
    assert order == ["score", "market"]
    assert result["code"] == "MODEL_SCORER_FAILED"
    assert result["llp_rundown_market_evidence"]["reason_code"] == "RUNDOWN_HTTP_503"
    assert "MODEL_UNAVAILABLE" not in str(result)


def test_favorite_conflict_blocks_market_relative_rank_not_probability(monkeypatch):
    monkeypatch.setattr(
        bridge,
        "resolve_rundown_market_context",
        lambda req: _exact_context(home=0.65, away=0.35),
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
    assert result["calibrated_home_probability"] == pytest.approx(0.70)
    assert result["rank_eligible"] is False
    assert "FAVORITE_STATUS_CONFLICT" in result["blockers"]
    assert result["market_role_status"] == "SOURCE_CONFLICT"
    assert result["llp_rundown_market_evidence"]["favorite_status_conflict"] is True


def test_winner_intent_preserves_rank_on_market_role_disagreement(monkeypatch):
    monkeypatch.setattr(
        bridge,
        "resolve_rundown_market_context",
        lambda req: _exact_context(home=0.65, away=0.35),
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
    monkeypatch.setattr(
        bridge,
        "resolve_rundown_market_context",
        lambda req: {"status": "DISABLED", "provider": bridge.BRIDGE_SOURCE},
    )

    def score(req, *, event_api, canonical_hydration_required=False):
        return {"can_execute": False}

    module = SimpleNamespace(score_team_event_request=score)
    assert bridge.install_llp_rundown_market_bridge(module) is True
    wrapped = module.score_team_event_request
    assert bridge.install_llp_rundown_market_bridge(module) is True
    assert module.score_team_event_request is wrapped
