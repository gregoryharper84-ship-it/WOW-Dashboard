from types import SimpleNamespace

from v17.llp_rundown_value_bridge import install_llp_rundown_value_bridge


class Request:
    def __init__(self, intent="WINNER"):
        self.home_team = "Home"
        self.away_team = "Away"
        self.decision_intent = intent
        self.market_prior = {
            "home_probability": 0.60,
            "away_probability": 0.40,
            "source": "CALLER",
        }


def _context(*, favorite="Home"):
    return {
        "status": "EXACT_LINE",
        "provider": "RUNDOWN_MARKET_EVIDENCE",
        "favorite": favorite,
        "current": {
            "snapshot_kind": "CURRENT",
            "moneyline": {
                "home_quotes": [
                    {"bookmaker": "Pinnacle", "american_odds": -125, "updated_at": "2026-09-22T20:00:00Z"},
                ],
                "away_quotes": [
                    {"bookmaker": "Pinnacle", "american_odds": 110, "updated_at": "2026-09-22T20:00:00Z"},
                ],
            },
        },
        "opening": None,
        "prediction_authority": False,
        "can_execute": False,
    }


def test_market_is_resolved_after_score_and_market_prior_is_not_mutated():
    req = Request()
    order = []
    seen_prior = {}

    def score(request, *, event_api, canonical_hydration_required=False):
        order.append("score")
        seen_prior.update(request.market_prior)
        return {
            "calibrated_home_probability": 0.62,
            "calibrated_away_probability": 0.38,
            "calibrated_home_lower_bound": 0.59,
            "calibrated_away_lower_bound": 0.35,
            "rank_eligible": True,
            "can_execute": False,
        }

    def resolve(request):
        order.append("market")
        assert request.market_prior == seen_prior
        return _context()

    module = SimpleNamespace(score_team_event_request=score)
    assert install_llp_rundown_value_bridge(module, resolver=resolve) is True
    result = module.score_team_event_request(req, event_api=object())

    assert order == ["score", "market"]
    assert req.market_prior["source"] == "CALLER"
    assert result["calibrated_home_probability"] == 0.62
    assert result["llp_rundown_market_evidence"]["market_prior_mutated_by_bridge"] is False
    assert result["llp_market_value"]["probability_rank_mutated"] is False
    assert result["can_execute"] is False


def test_market_failure_preserves_completed_sporting_probability():
    def score(request, *, event_api, canonical_hydration_required=False):
        return {
            "probability_publishable": True,
            "rank_eligible": True,
            "calibrated_home_probability": 0.62,
            "calibrated_away_probability": 0.38,
            "calibrated_home_lower_bound": 0.59,
            "calibrated_away_lower_bound": 0.35,
            "can_execute": False,
        }

    module = SimpleNamespace(score_team_event_request=score)
    install_llp_rundown_value_bridge(
        module,
        resolver=lambda req: {
            "status": "MARKET_DATA_UNOBTAINABLE",
            "reason_code": "RUNDOWN_HTTP_403",
            "prediction_authority": False,
            "can_execute": False,
        },
    )
    result = module.score_team_event_request(Request(), event_api=object())
    assert result["probability_publishable"] is True
    assert result["rank_eligible"] is True
    assert result["calibrated_home_probability"] == 0.62
    assert result["llp_market_value"]["status"] == "MARKET_DATA_UNAVAILABLE"
    assert result["llp_market_value"]["reason_code"] == "RUNDOWN_HTTP_403"


def test_favorite_conflict_blocks_only_market_relative_intent():
    def score(request, *, event_api, canonical_hydration_required=False):
        return {
            "rank_eligible": True,
            "calibrated_home_probability": 0.62,
            "calibrated_away_probability": 0.38,
            "calibrated_home_lower_bound": 0.59,
            "calibrated_away_lower_bound": 0.35,
            "can_execute": False,
        }

    module = SimpleNamespace(score_team_event_request=score)
    install_llp_rundown_value_bridge(module, resolver=lambda req: _context(favorite="Away"))
    result = module.score_team_event_request(Request(intent="UNDERDOG"), event_api=object())
    assert result["rank_eligible"] is False
    assert "FAVORITE_STATUS_CONFLICT" in result["blockers"]
    assert result["calibrated_home_probability"] == 0.62
