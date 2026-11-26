"""NHL Action material-context invalidation negatives — Incident #1952.

Proves starting-goalie / injury material updates after a forecast timestamp
cannot leave an NHL moneyline package publishable. Relies on the shared
hold_scored_after_material_change / assess_context_change_impact contracts;
does not mint probabilities or flip can_execute.
"""
from __future__ import annotations

from datetime import datetime, timezone

from v17.context_change_impact import (
    assess_context_change_impact,
    hold_scored_after_material_change,
)

NOW = datetime(2026, 11, 26, 17, 5, tzinfo=timezone.utc)
NHL_SPORT = "icehockey_nhl"
EVENT_ID = "2026020400"


def _nhl_event():
    return {
        "sport_key": NHL_SPORT,
        "official_event_id": EVENT_ID,
        "commence_time": "2026-11-27T18:00:00Z",
        "home_team": "BOS",
        "away_team": "TOR",
        "can_execute": False,
    }


def _goalie_handoff(*, subject="HOME_GOALIE"):
    return {
        "sport_key": NHL_SPORT,
        "official_event_id": EVENT_ID,
        "event_start_utc": "2026-11-27T18:00:00+00:00",
        "research_status": "NEWS_RESEARCH_RESCORE_REQUESTED",
        "research_rescore_requested": True,
        "receipt_sha256": "c" * 64,
        "news_context": [{
            "source_url": "https://www.nhl.com/news/goalie-change",
            "independent_review_ref": "nhl-goalie-review-1",
            "independently_reviewed_at_utc": "2026-11-26T17:00:00+00:00",
            "material_change": True,
            "subject_id": subject,
            "evidence_kind": "STARTING_GOALIE_STATUS",
        }],
        "can_execute": False,
        "prediction_authority": False,
    }


def _nhl_scored_package(*, model_at: str, publishable: bool = True):
    return {
        "sport": "NHL",
        "sport_key": NHL_SPORT,
        "official_event_id": EVENT_ID,
        "market_family": "MONEYLINE_WINNER",
        "market_period": "FULL_GAME",
        "immutable_model_timestamp": model_at,
        "calibrated_probability": 0.58,
        "calibrated_lower_bound": 0.41,
        "rank_eligible": publishable,
        "probability_publishable": publishable,
        "can_execute": False,
        "fitted_registry_scoring": True,
    }


def test_nhl_starting_goalie_change_requests_event_wide_rescore():
    impact = assess_context_change_impact(
        _nhl_event(), _goalie_handoff(), now=NOW
    )
    assert impact["impact_status"] == "CONTEXT_STALE_RECALC_REQUIRED"
    assert impact["research_invalidation_requested"] is True
    assert "MONEYLINE_WINNER" in impact["affected_market_families"]
    assert impact["fitted_specialist_rescore_verified"] is False
    assert impact["model_probability_created"] is False
    assert impact["can_execute"] is False


def test_nhl_injury_change_also_invalidates_moneyline():
    handoff = _goalie_handoff(subject="STAR_FORWARD")
    handoff["news_context"][0]["evidence_kind"] = "INJURY_STATUS"
    impact = assess_context_change_impact(_nhl_event(), handoff, now=NOW)
    assert impact["impact_status"] == "CONTEXT_STALE_RECALC_REQUIRED"
    assert "MONEYLINE_WINNER" in impact["affected_market_families"]
    assert impact["can_execute"] is False


def test_nhl_after_forecast_goalie_update_downgrades_publishable_package():
    """Changed starting goalie after forecast generation cannot stay publishable."""
    scored = _nhl_scored_package(model_at="2026-11-26T16:00:00Z", publishable=True)
    held = hold_scored_after_material_change(
        scored,
        latest_material_update_timestamp="2026-11-26T17:00:00Z",
    )
    assert held["material_context_chronology_status"] == "CONTEXT_STALE_RECALC_REQUIRED"
    assert held["probability_publishable"] is False
    assert held["rank_eligible"] is False
    assert "CONTEXT_STALE_RECALC_REQUIRED" in held["publication_blockers"]
    assert held["calibrated_probability"] == 0.58
    assert held["can_execute"] is False
    # Pure: original package untouched.
    assert scored["probability_publishable"] is True


def test_nhl_material_update_cannot_upgrade_already_held_package():
    scored = _nhl_scored_package(model_at="2026-11-26T16:00:00Z", publishable=False)
    held = hold_scored_after_material_change(
        scored,
        latest_material_update_timestamp="2026-11-26T17:00:00Z",
    )
    assert held["probability_publishable"] is False
    assert held["rank_eligible"] is False
    assert held["can_execute"] is False
