"""Research-only fallback tests: no numeric output may become governed probability."""
from __future__ import annotations

from datetime import datetime, timezone

from fastapi import FastAPI
from fastapi.testclient import TestClient

from v17.web_intelligence_fallback import research_fallback
from v17.web_intelligence_fallback_routes import install_web_intelligence_fallback_routes

NOW = datetime(2026, 10, 25, 13, 0, tzinfo=timezone.utc)
FACTORS = (
    "TEAM_STRENGTH", "INJURIES_LINEUPS", "OPPONENT_MATCHUP",
    "PACE_GAME_SCRIPT", "VENUE_FATIGUE",
)


def example():
    return {
        "sport": "NBA", "official_event_id": "game-25", "event_start_time_utc": "2026-10-25T23:00:00Z",
        "market_type": "SPREAD", "selection": "Detroit +1.5", "line": 1.5,
        "model_status": "MODEL_UNAVAILABLE",
        "evidence": [
            {"factor": factor, "favorability": 0.75, "source_group": f"independent-{i}",
             "source_class": "ESTABLISHED_STATS_PROVIDER",
             "url": f"https://stats.example.org/{i}", "claim": "Pregame matchup observation",
             "observed_at": "2026-10-25T12:30:00Z", "confirmed": True}
            for i, factor in enumerate(FACTORS)
        ],
        "market_quotes": [
            {"event_id": "game-25", "market_type": "SPREAD", "selection": "Detroit +1.5",
             "line": 1.5, "american_odds": 110, "observed_at": "2026-10-25T12:57:00Z", "book": "demo"}
        ],
    }


def test_complete_research_outputs_only_research_score_and_exact_math():
    result = research_fallback(example(), now=NOW)
    assert result["status"] == "RESEARCH_INTEREST"
    assert 50 < result["research_score"] < 100
    assert result["market_audit"][0]["break_even_probability"] == 0.47619
    assert result["calibrated_probability"] is None
    assert result["calibrated_lower_bound"] is None
    assert result["raw_model_probability"] is None
    assert result["probability_publishable"] is False
    assert result["rank_eligible"] is False
    assert result["terminal_label"] is None
    assert result["can_execute"] is False


def test_market_quote_is_never_probability_and_cannot_influence_research_score():
    request = example()
    before = research_fallback(request, now=NOW)["research_score"]
    request["market_quotes"][0]["american_odds"] = -145
    after = research_fallback(request, now=NOW)
    assert after["research_score"] == before
    assert after["market_audit"][0]["break_even_probability"] != 0.47619


def test_wrong_exact_spread_line_or_stale_quote_not_verified():
    request = example()
    request["market_quotes"][0]["line"] = 2.5
    assert research_fallback(request, now=NOW)["market_audit"][0]["break_even_probability"] is None
    request["market_quotes"][0]["line"] = 1.5
    request["market_quotes"][0]["observed_at"] = "2026-10-25T11:00:00Z"
    assert research_fallback(request, now=NOW)["market_audit"][0]["break_even_probability"] is None


def test_no_research_from_missing_component():
    request = example()
    request["evidence"].pop()
    result = research_fallback(request, now=NOW)
    assert result["status"] == "INSUFFICIENT_RESEARCH_EVIDENCE"
    assert result["research_score"] is None


def test_conflicting_independent_news_does_not_average_away_warning():
    request = example()
    duplicate = dict(request["evidence"][0], source_group="second-source", favorability=0.1)
    request["evidence"].append(duplicate)
    result = research_fallback(request, now=NOW)
    assert result["status"] == "RESEARCH_EVIDENCE_CONFLICT"
    assert result["research_score"] is None


def test_market_source_cannot_satisfy_a_sporting_factor():
    request = example()
    request["evidence"][0]["source_class"] = "SPORTSBOOK_FEED"
    result = research_fallback(request, now=NOW)
    assert result["status"] == "INSUFFICIENT_RESEARCH_EVIDENCE"
    assert result["rejected_evidence"][0]["reason"] == "SPORTING_SOURCE_UNSUPPORTED"


def test_missing_confirmation_or_bad_timestamp_is_rejected():
    request = example()
    request["evidence"][0].update(source_class="PRIMARY_BEAT_REPORTER", confirmed=False)
    request["evidence"][1]["observed_at"] = "2026-10-25T13:30:00Z"
    result = research_fallback(request, now=NOW)
    assert result["status"] == "INSUFFICIENT_RESEARCH_EVIDENCE"
    assert len(result["rejected_evidence"]) == 2


def test_duplicate_same_source_is_not_double_counted():
    request = example()
    request["evidence"].append(dict(request["evidence"][0]))
    result = research_fallback(request, now=NOW)
    assert result["status"] == "RESEARCH_INTEREST"
    assert len(result["accepted_evidence"]) == 5


def test_event_started_other_sports_or_model_success_never_create_result():
    request = example()
    request["event_start_time_utc"] = "2026-10-25T12:59:00Z"
    assert research_fallback(request, now=NOW)["status"] == "EVENT_NOT_PREGAME"
    request = example()
    request["sport"] = "BADMINTON"
    assert research_fallback(request, now=NOW)["status"] == "RESEARCH_PROFILE_UNSUPPORTED"
    request = example()
    request["model_status"] = "PASS"
    assert research_fallback(request, now=NOW)["status"] == "FALLBACK_NOT_APPLICABLE"


def test_no_public_anonymous_research_endpoint_and_no_unapproved_probability_fields():
    app = FastAPI()
    def auth():
        from fastapi import HTTPException
        raise HTTPException(status_code=401)
    assert install_web_intelligence_fallback_routes(app, auth_dependency=auth)
    assert install_web_intelligence_fallback_routes(app, auth_dependency=auth)
    client = TestClient(app)
    response = client.post("/v17/research/web-intelligence-fallback", json=example())
    assert response.status_code == 401
    assert sum(route.path == "/v17/research/web-intelligence-fallback" for route in app.routes) == 1


def test_authenticated_endpoint_can_research_but_rejects_probability_injection():
    app = FastAPI()
    install_web_intelligence_fallback_routes(app, auth_dependency=lambda: None)
    client = TestClient(app)
    sample = example()
    # Current time is injected only for pure unit tests; endpoint enforces real clock.
    sample["calibrated_probability"] = 0.99
    assert client.post("/v17/research/web-intelligence-fallback", json=sample).status_code == 422
