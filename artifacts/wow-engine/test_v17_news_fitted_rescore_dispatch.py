"""News → fitted rescore dispatch: success path and adversarial holds."""
from __future__ import annotations

from datetime import datetime, timezone

from v17.news_fitted_rescore_dispatch import (
    build_news_feature_snapshot,
    dispatch_news_rescore,
)

NOW = datetime(2026, 11, 22, 18, 0, tzinfo=timezone.utc)
MATERIAL = [{
    "evidence_kind": "STARTING_GOALIE_STATUS",
    "subject_id": "NYI-G",
    "fact_value": "SOROKIN",
    "source_class": "LEAGUE_OFFICIAL",
    "source_group": "nhl-official",
    "source_url": "https://www.nhl.com/news/islanders-confirm-starting-goalie",
    "independent_review_ref": "sirt-review-1",
    "publisher_published_at_utc": "2026-11-22T16:00:00+00:00",
    "independently_reviewed_at_utc": "2026-11-22T17:00:00+00:00",
    "material_change": True,
}]


def _candidate(**overrides):
    row = {
        "sport_key": "icehockey_nhl",
        "official_event_id": "2026020381",
        "provider_event_id": "espn-401999999",
        "official_event_identity_verified": True,
        "canonical_identity_status": "CANONICAL_RESOLVED",
        "event_start_utc": "2026-11-24T00:30:00Z",
        "home_team": "New York Islanders",
        "away_team": "Toronto Maple Leafs",
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
            "event_start_utc": "2026-11-24T00:30:00+00:00",
        },
    }
    row.update(overrides)
    return row


def _ok_scorer(request):
    return {
        "ok": True,
        "code": "NEWS_FITTED_RESCORE_COMPLETE",
        "model_version": "nhl-llp-fixture-v17.test",
        "feature_snapshot_hash": request["feature_snapshot_hash"],
        "calibrated_probability": 0.61,
        "calibrated_lower_bound": 0.54,
        "prediction_authority": False,
        "can_execute": False,
    }


def test_feature_snapshot_hash_changes_when_material_news_is_added():
    before = build_news_feature_snapshot({"goalie_status": "UNCONFIRMED"}, [])
    after = build_news_feature_snapshot({"goalie_status": "UNCONFIRMED"}, MATERIAL)
    assert before["feature_snapshot_hash"] != after["feature_snapshot_hash"]
    assert after["news_fact_count"] == 1
    assert after["can_execute"] is False


def test_genuine_scorer_response_verifies_rescore_with_immutable_receipt():
    out = dispatch_news_rescore(
        _candidate(),
        score_fn=_ok_scorer,
        observed_at=NOW,
        baseline_features={"goalie_status": "UNCONFIRMED"},
    )
    assert out["dispatch_status"] == "NEWS_RESCORE_VERIFIED"
    assert out["model_rescore_verified"] is True
    assert out["scoring_attempted"] is True
    assert out["feature_snapshot_changed"] is True
    assert out["before_feature_snapshot_hash"] != out["after_feature_snapshot_hash"]
    assert out["fitted_model_version"] == "nhl-llp-fixture-v17.test"
    assert out["calibrated_probability"] == 0.61
    assert out["calibrated_lower_bound"] == 0.54
    assert out["can_execute"] is False
    assert out["prediction_authority"] is False
    assert len(out["receipt_sha256"]) == 64


def test_forged_provider_id_and_unverified_identity_cannot_rescore():
    forged = _candidate(
        official_event_identity_verified=False,
        canonical_identity_status="PROVIDER_EVENT_ID_UNVERIFIED",
        official_event_id="espn-401999999",
        provider_event_id="espn-401999999",
    )
    out = dispatch_news_rescore(forged, score_fn=_ok_scorer, observed_at=NOW)
    assert out["dispatch_status"] == "NEWS_RESCORE_IDENTITY_UNVERIFIED"
    assert out["model_rescore_verified"] is False
    assert out["scoring_attempted"] is False


def test_started_event_and_stale_path_fail_closed():
    started = _candidate(event_start_utc="2026-11-22T17:00:00Z")
    out = dispatch_news_rescore(started, score_fn=_ok_scorer, observed_at=NOW)
    assert out["dispatch_status"] == "NEWS_RESCORE_EVENT_ALREADY_STARTED"
    assert out["model_rescore_verified"] is False


def test_missing_fitted_model_is_typed_unavailable_not_synthetic_probability():
    out = dispatch_news_rescore(
        _candidate(),
        score_fn=None,
        observed_at=NOW,
        baseline_features={"goalie_status": "UNCONFIRMED"},
    )
    assert out["dispatch_status"] == "NEWS_RESCORE_MODEL_UNAVAILABLE"
    assert out["scoring_attempted"] is True
    assert out["model_rescore_verified"] is False
    assert out["calibrated_probability"] is None
    assert out["can_execute"] is False


def test_scorer_claiming_execution_authority_is_rejected():
    def bad(_request):
        return {
            "ok": True,
            "code": "NEWS_FITTED_RESCORE_COMPLETE",
            "model_version": "x",
            "can_execute": True,
            "prediction_authority": False,
        }

    out = dispatch_news_rescore(
        _candidate(),
        score_fn=bad,
        observed_at=NOW,
        baseline_features={"goalie_status": "UNCONFIRMED"},
    )
    assert out["dispatch_status"] == "NEWS_RESCORE_AUTHORITY_REJECTED"
    assert out["model_rescore_verified"] is False
