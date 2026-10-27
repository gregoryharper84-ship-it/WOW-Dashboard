"""Bounded, no-network synthetic tests for the shadow challenger lab."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import hashlib

import pytest
from v17.accuracy_shadow_challenger_lab import train_shadow_challenger, ChallengerLabError


def fixture(ntrain=30, ncal=20, nhold=30):
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    samples = []
    for i in range(ntrain + ncal + nhold):
        stamp = start + timedelta(days=i)
        split = "TRAIN" if i < ntrain else ("CALIBRATION" if i < ntrain+ncal else "HOLDOUT")
        samples.append({
            "event_id": "event-%03d" % i, "market_id": "market-%03d" % i,
            "sport": "NBA", "route": "NBA_ASSISTS",
            "split": split, "event_start_utc": stamp.isoformat(),
            "feature_asof_utc": (stamp-timedelta(hours=2)).isoformat(),
            "champion_locked_at_utc": (stamp-timedelta(hours=1)).isoformat(),
            "settled_at_utc": (stamp+timedelta(hours=3)).isoformat(),
            "outcome": i % 2, "champion_probability": 0.46 + (i%3)*0.035,
            "champion_lower_bound": 0.39, "champion_origin": "FITTED_SPECIALIST",
            "champion_artifact_id": "certified-model-1",
            "champion_calibrator_id": "calibrator-1",
            "champion_specialist_id": "NBA_ASSISTS_FITTED_V17",
            "champion_prediction_id": "prediction-%03d" % i,
            "source_id": "OFFLINE_FIXTURE", "settlement_source_id": "FIXTURE_OFFICIAL",
            "source_sha256": hashlib.sha256(("data-%d" % i).encode()).hexdigest(),
            "features": {"pace_prior": float(i%9), "recent_assists_prior": float(i%7)},
        })
    return {"experiment_id": "SHADOW_FIXTURE", "sport": "NBA", "route": "NBA_ASSISTS",
            "feature_names": ["pace_prior", "recent_assists_prior"], "rows": samples}


def test_research_training_is_deterministic_bounded_and_nonpublishable():
    result = train_shadow_challenger(fixture())
    same = train_shadow_challenger(fixture())
    assert result == same
    assert result["counts"] == {"TRAIN":30, "CALIBRATION":20, "HOLDOUT":30}
    assert result["holdout_event_count"] == 30
    assert result["champion_metrics"]["n"] == 30
    assert result["challenger_metrics"]["n"] == 30
    assert len(result["paired_brier_delta_event_cluster_bootstrap_ci"]) == 2
    assert result["source_authentication_proven"] is False
    assert result["automatic_promotion"] is False
    assert result["probability_publishable"] is False
    assert result["can_execute"] is False


def test_feature_leakage_market_odds_and_final_results_blocked():
    for forbidden in ("sportsbook_odds", "closing_line", "postgame_win"):
        p = fixture()
        p["feature_names"] = [forbidden]
        with pytest.raises(ChallengerLabError, match="MARKET_OR_POSTGAME_FEATURE_LEAKAGE"):
            train_shadow_challenger(p)


def test_started_event_feature_leakage_rejected():
    p=fixture()
    p["rows"][0]["feature_asof_utc"] = p["rows"][0]["event_start_utc"]
    with pytest.raises(ChallengerLabError, match="POINT_IN_TIME_LEAKAGE"):
        train_shadow_challenger(p)


def test_same_event_different_offer_cannot_cross_splits():
    p=fixture()
    p["rows"][30]["event_id"] = p["rows"][0]["event_id"]
    with pytest.raises(ChallengerLabError, match="CROSS_SPLIT_EVENT_LEAKAGE"):
        train_shadow_challenger(p)


def test_calibration_cannot_overlap_training_chronologically():
    p=fixture()
    p["rows"][0]["event_start_utc"] = "2026-11-15T00:00:00Z"
    p["rows"][0]["feature_asof_utc"] = "2026-11-14T22:00:00Z"
    p["rows"][0]["champion_locked_at_utc"] = "2026-11-14T23:00:00Z"
    p["rows"][0]["settled_at_utc"] = "2026-11-15T03:00:00Z"
    with pytest.raises(ChallengerLabError, match="CHRONOLOGICAL_SPLIT_LEAKAGE"):
        train_shadow_challenger(p)


def test_bad_source_digest_refused():
    p=fixture()
    p["rows"][0]["source_sha256"] = "synthetic"
    with pytest.raises(ChallengerLabError, match="SOURCE_DIGEST_REQUIRED"):
        train_shadow_challenger(p)


def test_insufficient_unseen_games_refused():
    p=fixture(nhold=29)
    with pytest.raises(ChallengerLabError, match="INSUFFICIENT_UNSEEN_HOLDOUT_EVENTS"):
        train_shadow_challenger(p)


def test_duplicate_row_refused():
    p=fixture()
    p["rows"].append(deepcopy(p["rows"][0]))
    with pytest.raises(ChallengerLabError, match="DUPLICATE_EVENT_MARKET"):
        train_shadow_challenger(p)


def test_missing_outcome_and_negative_probabilities_refused():
    p=fixture()
    p["rows"][0]["outcome"] = None
    with pytest.raises(ChallengerLabError, match="BINARY_OUTCOME_REQUIRED"):
        train_shadow_challenger(p)
    p=fixture()
    p["rows"][0]["champion_probability"] = -0.1
    with pytest.raises(ChallengerLabError, match="CHAMPION_PROBABILITY_INVALID"):
        train_shadow_challenger(p)


def test_no_unrecognized_trainer_fallback_to_narrative_probability():
    with pytest.raises(ChallengerLabError, match="UNSUPPORTED_SHADOW_TRAINER"):
        train_shadow_challenger(fixture(),engine="LLM_REASONING")

def test_market_implied_probability_cannot_be_champion():
    p = fixture()
    p["rows"][0]["champion_origin"] = "SPORTSBOOK_IMPLIED"
    with pytest.raises(ChallengerLabError, match="MARKET_OR_NARRATIVE_NOT_FITTED_CHAMPION"):
        train_shadow_challenger(p)


def test_mixed_incumbent_artifact_rejected_from_paired_replay():
    p = fixture()
    p["rows"][0]["champion_artifact_id"] = "different-model"
    with pytest.raises(ChallengerLabError, match="MIXED_CHAMPION_MODEL_LINEAGE"):
        train_shadow_challenger(p)


def test_invalid_champion_calibrated_lower_bound_rejected():
    p = fixture()
    p["rows"][0]["champion_lower_bound"] = 0.99
    with pytest.raises(ChallengerLabError, match="CHAMPION_LOWER_BOUND_INVALID"):
        train_shadow_challenger(p)
