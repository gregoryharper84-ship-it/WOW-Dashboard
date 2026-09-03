from __future__ import annotations

from types import SimpleNamespace

import pytest

from v17.projected_lineup_probability_rehydration import rehydrate_projected_probability
from v17.projected_lineup_scenario_modeling import projected_probability_hold


class Query:
    def __init__(self, row): self.row = row
    def select(self, *args, **kwargs): return self
    def eq(self, *args, **kwargs): return self
    def limit(self, *args, **kwargs): return self
    def execute(self): return SimpleNamespace(data=[dict(self.row)])


class Client:
    def __init__(self, row): self.row = row
    def table(self, name):
        assert name == "wow_mlb_forward_score_snapshots"
        return Query(self.row)


def receipt(**updates):
    base = {
        "code": "REAL_FITTED_MODEL_PATH_PROVEN",
        "probability_fields_withheld": True,
        "scoring_evidence_produced": True,
        "governed_probability_capability": "AVAILABLE",
        "score_status": "SHADOW_SCORED_LINEUP_PENDING",
        "lineup_status": "NOT_YET_AVAILABLE",
        "ratification_status": "RATIFIED",
        "calibration_health_status": "PASS",
        "feature_hydration_status": "PASS",
        "score_snapshot_id": "score-1",
        "shadow_event_id": "shadow-1",
        "server_snapshot_id": "source-1",
        "current_publication_blockers": [
            "LINEUP_NOT_CONFIRMED",
            "OFFICIAL_LINEUP_REFRESH_OFFICIAL_LINEUP_NOT_AVAILABLE",
            "POST_LINEUP_SCORE_SNAPSHOT_REQUIRED",
        ],
        "probability_publishable": False,
        "can_execute": False,
    }
    base.update(updates)
    return base


def score(**updates):
    base = {
        "score_snapshot_id": "score-1",
        "shadow_event_id": "shadow-1",
        "model_timestamp": "2026-09-03T02:42:58+00:00",
        "model_version": "MLB_V2C_SHARED_NB_2024_R1",
        "calibration_id": "0756545d-4ef5-47b7-950a-53567f0bf9fe",
        "calibration_method": "LOGIT_INTERCEPT_POOLED_2022_2024",
        "raw_home_probability": 0.49,
        "raw_away_probability": 0.51,
        "calibrated_home_probability": 0.516,
        "calibrated_away_probability": 0.484,
        "home_lower_bound": 0.365,
        "home_upper_bound": 0.67,
        "away_lower_bound": 0.384,
        "away_upper_bound": 0.64,
        "home_bound_status": "PASS_RESEARCH_BOUND",
        "away_bound_status": "PASS_RESEARCH_BOUND",
        "tie_after_9_probability": 0.10,
        "lineup_status_at_score": "NOT_YET_AVAILABLE",
        "score_status": "SHADOW_SCORED_LINEUP_PENDING",
        "blockers": ["LINEUP_NOT_CONFIRMED"],
        "probability_publishable": False,
        "can_execute": False,
    }
    base.update(updates)
    return base


def req():
    return SimpleNamespace(
        latest_material_update_timestamp="2026-09-03T02:42:33+00:00",
        sport_specific_evidence={
            "home_lineup_status": "PROJECTED_HIGH_CONFIDENCE",
            "away_lineup_status": "PROJECTED_HIGH_CONFIDENCE",
        },
    )


def event_api(row):
    return SimpleNamespace(get_client=lambda: Client(row))


def test_eligible_held_receipt_rehydrates_exact_immutable_score_and_calibration_provenance():
    result = rehydrate_projected_probability(receipt(), req(), event_api=event_api(score()))
    assert result["raw_home_probability"] == pytest.approx(0.49)
    assert result["raw_away_probability"] == pytest.approx(0.51)
    assert result["calibrated_home_probability"] == pytest.approx(0.516)
    assert result["calibrated_home_lower_bound"] == pytest.approx(0.365)
    assert result["calibration_method"] == "LOGIT_INTERCEPT_POOLED_2022_2024"
    assert result["calibration_version"] == "0756545d-4ef5-47b7-950a-53567f0bf9fe"
    assert result["calibration_sample_scope"].startswith("IMMUTABLE_FORWARD_SCORE_SNAPSHOT:")
    assert result["probability_fields_withheld"] is False
    assert result["sporting_probability_completed"] is True
    repair = result["projected_lineup_score_rehydration"]
    assert repair["score_snapshot_id"] == "score-1"
    assert repair["calibration_id"] == result["calibration_version"]
    assert repair["probabilities_recomputed"] is False
    assert repair["calibration_recomputed"] is False
    assert result["can_execute"] is False


def test_rehydrated_package_enters_projected_probability_hold_not_rank_publication():
    hydrated = rehydrate_projected_probability(receipt(), req(), event_api=event_api(score()))
    projected = projected_probability_hold(req(), hydrated, {"status": "HOLD"})
    assert projected is not None
    assert projected["code"] == "LINEUP_PROJECTED_PROBABILITY_AVAILABLE"
    assert projected["sporting_probability_publishable"] is True
    assert projected["probability_publishable"] is True
    assert projected["rank_eligible"] is False
    assert projected["final_refresh_required"] is True
    assert "LINEUP_CONFIRMATION_PENDING" in projected["blockers"]
    assert projected["can_execute"] is False


def test_missing_calibration_identity_prevents_rehydration():
    original = receipt()
    result = rehydrate_projected_probability(original, req(), event_api=event_api(score(calibration_id=None)))
    assert result == original


def test_unratified_receipt_remains_held_and_numeric_fields_absent():
    original = receipt(ratification_status="NOT_RATIFIED")
    result = rehydrate_projected_probability(original, req(), event_api=event_api(score()))
    assert result == original
    assert "raw_home_probability" not in result


def test_non_lineup_publication_blocker_prevents_rehydration():
    original = receipt(current_publication_blockers=["MODEL_ARTIFACT_UNAVAILABLE"])
    result = rehydrate_projected_probability(original, req(), event_api=event_api(score()))
    assert result == original


def test_snapshot_identity_mismatch_prevents_rehydration():
    original = receipt()
    result = rehydrate_projected_probability(original, req(), event_api=event_api(score(score_snapshot_id="other")))
    assert result == original
    assert "calibrated_home_probability" not in result


def test_invalid_bounds_prevent_rehydration():
    original = receipt()
    bad = score(home_lower_bound=0.80)
    result = rehydrate_projected_probability(original, req(), event_api=event_api(bad))
    assert result == original


def test_post_material_update_score_required():
    stale_req = req()
    stale_req.latest_material_update_timestamp = "2026-09-03T03:00:00+00:00"
    result = rehydrate_projected_probability(receipt(), stale_req, event_api=event_api(score()))
    assert "raw_home_probability" not in result
