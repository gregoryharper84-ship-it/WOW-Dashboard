"""Adversarial multi-sport fallback tests: research scores are never model probabilities."""
from __future__ import annotations

from datetime import datetime, timezone
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from v17.web_intelligence_fallback import research_fallback
from v17.web_intelligence_fallback_routes import install_web_intelligence_fallback_routes
from v17.web_intelligence_scout_bridge import from_scout_candidate
from v17.web_intelligence_sport_profiles import (
    WEIGHT_PROFILES, MARKETS, DOMAIN_FACTORS, normalize_sport,
)

NOW = datetime(2026, 10, 25, 13, 0, tzinfo=timezone.utc)


def sample(sport: str):
    factor_weights = WEIGHT_PROFILES[sport]
    market_type = sorted(MARKETS[sport])[0]
    line = 1.5 if market_type in {"SPREAD", "TOTAL"} else None
    return {
        "sport": sport,
        "official_event_id": sport + "-fixture",
        "event_start_time_utc": "2026-10-25T23:00:00Z",
        "market_type": market_type,
        "selection": sport + "-A",
        "line": line, "model_status": "MODEL_UNAVAILABLE",
        "evidence": [
            {"factor": factor, "favorability": .75, "source_group": "publisher-"+str(i),
             "source_class": "ESTABLISHED_STATS_PROVIDER",
             "url": "https://research.example.org/"+str(i),
             "claim": "Pregame structured performance input",
             "observed_at": "2026-10-25T12:40:00Z", "confirmed": True}
            for i, factor in enumerate(factor_weights)
        ],
        "market_quotes": [],
    }


def test_all_declared_sports_have_distinct_research_profiles_and_no_predictions():
    assert len(WEIGHT_PROFILES) >= 13
    for sport in WEIGHT_PROFILES:
        result = research_fallback(sample(sport), now=NOW)
        assert result["status"] == "RESEARCH_INTEREST", sport
        assert set(result["factor_weights"]) == set(WEIGHT_PROFILES[sport])
        assert 50 < result["research_score"] < 100
        assert result["calibrated_probability"] is None
        assert result["calibrated_lower_bound"] is None
        assert result["probability_publishable"] is False
        assert result["rank_eligible"] is False
        assert result["can_execute"] is False
    assert WEIGHT_PROFILES["NFL"] != WEIGHT_PROFILES["NBA"]


def test_market_settlement_is_sport_specific_and_does_not_infer_two_way_soccer():
    soccer = sample("SOCCER")
    soccer["market_type"] = "MONEYLINE"
    result = research_fallback(soccer, now=NOW)
    assert result["status"] == "EXACT_MARKET_UNSUPPORTED"
    soccer["market_type"] = "THREE_WAY_1X2"
    assert research_fallback(soccer, now=NOW)["status"] == "RESEARCH_INTEREST"
    soccer["line"] = 1.5
    assert research_fallback(soccer, now=NOW)["status"] == "EXACT_MARKET_IDENTITY_INVALID"


def test_nfl_source_cannot_fill_nba_wrong_factor():
    n = sample("NFL")
    n["evidence"][0]["factor"] = "INJURIES_LINEUPS"
    result = research_fallback(n, now=NOW)
    assert result["status"] == "INSUFFICIENT_RESEARCH_EVIDENCE"
    assert result["rejected_evidence"][0]["reason"] == "FACTOR_UNSUPPORTED"


def test_aliases_and_all_sport_scouter_bridge():
    assert normalize_sport("basketball_wnba") == "WNBA"
    assert normalize_sport("UFC") == "MMA"
    assert normalize_sport("PGA") == "GOLF"
    assert normalize_sport("americanfootball_ncaaf") == "NCAAF"
    for sport, factors in DOMAIN_FACTORS.items():
        domains = {}
        for idx, (domain, factor) in enumerate(factors.items()):
            if factor in [x["factor"] for x in domains.values()]:
                continue
            domains[domain] = {
                "official_event_id": "event",
                "selection": "side",
                "directional_impact": "FAVORABLE",
                "source_class": "ESTABLISHED_STATS_PROVIDER",
                "source_provider": "source-"+str(idx),
                "source_url": "https://research.example.org/"+str(idx),
                "claim": "Prematch validated observation",
                "captured_at": "2026-10-25T12:40:00Z",
                "confirmed": True,
                "factor": factor,
            }
        assert set(x["factor"] for x in domains.values()) == set(WEIGHT_PROFILES[sport])
        candidate = {
            "sport_key": sport, "official_event_id": "event",
            "commence_time": "2026-10-25T23:00:00Z",
            "research_domain_evidence": domains,
        }
        market_type = "MONEYLINE" if "MONEYLINE" in MARKETS[sport] else sorted(MARKETS[sport])[0]
        line = 1.5 if market_type in {"SPREAD", "TOTAL"} else None
        result = from_scout_candidate(candidate, model_status="MODEL_UNAVAILABLE",
                                      market_type=market_type, selection="side", line=line, now=NOW)
        assert result["status"] == "RESEARCH_INTEREST", (sport, result)
        assert result["scout_source_rows_normalized"] == 5, sport
        assert result["prediction_authority"] is False


def test_scout_source_mismatch_and_conflict_fail_closed():
    sport = "NHL"
    domains = {}
    for idx, (key, factor) in enumerate(DOMAIN_FACTORS[sport].items()):
        if factor in [x["factor"] for x in domains.values()]:
            continue
        domains[key] = {
            "factor": factor, "official_event_id": "event",
            "selection": "side", "directional_impact": "FAVORABLE",
            "source_class": "ESTABLISHED_STATS_PROVIDER",
            "source_provider": "publisher-"+str(idx), "source_url": "https://research.example.org/"+str(idx),
            "claim": "Research observation", "captured_at": "2026-10-25T12:40:00Z",
            "confirmed": True,
        }
    candidate = {
        "sport_key": sport, "official_event_id": "event",
        "commence_time": "2026-10-25T23:00:00Z",
        "research_domain_evidence": domains,
    }
    first = next(iter(domains.values()))
    first["official_event_id"] = "wrong"
    result = from_scout_candidate(candidate, model_status="MODEL_UNAVAILABLE",
                                  market_type="MONEYLINE", selection="side", now=NOW)
    assert result["status"] == "INSUFFICIENT_RESEARCH_EVIDENCE"
    assert result["scout_source_rows_rejected"][0]["reason"] == "SOURCE_EVENT_IDENTITY_MISMATCH"
    first["official_event_id"] = "event"
    candidate["contradictory_evidence"] = ["Starting goalie ruled out"]
    result = from_scout_candidate(candidate, model_status="MODEL_UNAVAILABLE",
                                  market_type="MONEYLINE", selection="side", now=NOW)
    assert result["status"] == "RESEARCH_EVIDENCE_CONFLICT"
    assert result["research_score"] is None


def test_auth_required_and_probability_injection_rejected_on_scout_route():
    app = FastAPI()
    def unauthorized():
        raise HTTPException(status_code=401)
    install_web_intelligence_fallback_routes(app, auth_dependency=unauthorized)
    body = {"candidate": {}, "model_status": "MODEL_UNAVAILABLE",
            "market_type": "MONEYLINE", "selection": "side"}
    client = TestClient(app)
    assert client.post("/v17/research/web-intelligence-fallback/from-scout", json=body).status_code == 401
    app = FastAPI()
    install_web_intelligence_fallback_routes(app, auth_dependency=lambda: None)
    client = TestClient(app)
    body["calibrated_probability"] = .99
    assert client.post("/v17/research/web-intelligence-fallback/from-scout", json=body).status_code == 422
    body.pop("calibrated_probability")
    assert client.post("/v17/research/web-intelligence-fallback/from-scout", json=body).json()["probability_publishable"] is False
