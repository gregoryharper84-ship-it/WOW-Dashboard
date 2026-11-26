"""Production news fitted-scorer binding: registered vs unregistered routes."""
from __future__ import annotations

from datetime import datetime, timezone

from v17.news_fitted_rescore_dispatch import dispatch_news_rescore
from v17.news_fitted_scorer_binding import (
    clear_news_fitted_scorer_registry,
    production_news_fitted_scorer,
    register_news_fitted_scorer,
)

NOW = datetime(2026, 11, 22, 18, 0, tzinfo=timezone.utc)
MATERIAL = [{
    "evidence_kind": "QB_STATUS",
    "subject_id": "IND-QB",
    "fact_value": "OUT",
    "source_class": "LEAGUE_OFFICIAL",
    "source_group": "nfl-official",
    "source_url": "https://www.nfl.com/news/colts-qb-ruled-out",
    "independent_review_ref": "sirt-nfl-qb-20261122",
    "publisher_published_at_utc": "2026-11-22T16:00:00+00:00",
    "independently_reviewed_at_utc": "2026-11-22T17:00:00+00:00",
    "material_change": True,
}]


def _candidate():
    return {
        "sport_key": "americanfootball_nfl",
        "official_event_id": "2026112201",
        "provider_event_id": "espn-401547417",
        "official_event_identity_verified": True,
        "canonical_identity_status": "CANONICAL_RESOLVED",
        "event_start_utc": "2026-11-24T18:00:00Z",
        "home_team": "Indianapolis Colts",
        "away_team": "Houston Texans",
        "controlling_specialist_route": "LLP_TEAM_BETTING_ENGINE",
        "can_execute": False,
        "news_research_handoff": {
            "research_status": "NEWS_RESEARCH_RESCORE_REQUESTED",
            "research_rescore_requested": True,
            "receipt_sha256": "b" * 64,
            "source_receipt_sha256": "a" * 64,
            "news_context": MATERIAL,
            "prediction_authority": False,
            "can_execute": False,
            "event_start_utc": "2026-11-24T18:00:00+00:00",
        },
    }


def _ok_scorer(request):
    return {
        "ok": True,
        "code": "NEWS_FITTED_RESCORE_COMPLETE",
        "model_version": "nfl-llp-fixture-v17.bind",
        "feature_snapshot_hash": request["feature_snapshot_hash"],
        "calibrated_probability": 0.57,
        "calibrated_lower_bound": 0.49,
        "immutable_prediction_id": "pred-nfl-news-bind-001",
        "prior_immutable_prediction_id": "pred-nfl-original-001",
        "calibration_receipt_id": "calib-nfl-bind-001",
        "controlling_specialist_route": request.get("controlling_specialist_route"),
        "prediction_authority": False,
        "can_execute": False,
    }


def _ok_persistence(request, response):
    return {
        "ok": True,
        "authenticated": True,
        "immutable_prediction_id": response.get("immutable_prediction_id"),
        "prior_immutable_prediction_id": response.get("prior_immutable_prediction_id"),
        "prior_immutable_prediction_preserved": True,
        "calibration_receipt_id": response.get("calibration_receipt_id"),
        "official_event_id": request.get("official_event_id"),
        "terminal_readback": {
            "authority": "V17_TERMINAL_REDUCER",
            "status": "HOLD_RESEARCH_RESCORE",
            "can_execute": False,
        },
        "store_attestation": {
            "source": "TEST_DOUBLE_NOT_PRODUCTION",
            "read_at_utc": NOW.isoformat(),
        },
        "prediction_authority": False,
        "can_execute": False,
    }


def setup_function():
    clear_news_fitted_scorer_registry()


def teardown_function():
    clear_news_fitted_scorer_registry()


def test_unregistered_route_is_typed_unavailable_not_synthetic():
    response = production_news_fitted_scorer({
        "sport_key": "americanfootball_nfl",
        "controlling_specialist_route": "LLP_TEAM_BETTING_ENGINE",
        "feature_snapshot_hash": "c" * 64,
    })
    assert response["ok"] is False
    assert response["code"] == "MODEL_UNAVAILABLE"
    assert "NEWS_FITTED_SCORER_UNREGISTERED" in response["blockers"]
    assert response["calibrated_probability"] is None
    assert response["can_execute"] is False

    out = dispatch_news_rescore(
        _candidate(),
        score_fn=production_news_fitted_scorer,
        observed_at=NOW,
        baseline_features={"qb_status": "ACTIVE"},
    )
    assert out["dispatch_status"] == "NEWS_RESCORE_MODEL_UNAVAILABLE"
    assert out["model_rescore_verified"] is False


def test_registered_adapter_can_complete_verified_news_rescore():
    register_news_fitted_scorer(
        "americanfootball_nfl",
        "LLP_TEAM_BETTING_ENGINE",
        _ok_scorer,
    )
    out = dispatch_news_rescore(
        _candidate(),
        score_fn=production_news_fitted_scorer,
        persistence_readback_fn=_ok_persistence,
        observed_at=NOW,
        baseline_features={"qb_status": "ACTIVE"},
    )
    assert out["dispatch_status"] == "NEWS_RESCORE_VERIFIED"
    assert out["model_rescore_verified"] is True
    assert out["persistence_authenticated"] is True
    assert out["calibrated_probability"] == 0.57
    assert out["calibrated_lower_bound"] == 0.49
    assert out["immutable_prediction_id"] == "pred-nfl-news-bind-001"
    assert out["prior_immutable_prediction_id"] == "pred-nfl-original-001"
    assert out["can_execute"] is False
