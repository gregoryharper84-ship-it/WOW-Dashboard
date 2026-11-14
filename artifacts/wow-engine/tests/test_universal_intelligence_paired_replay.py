"""Synthetic paired holdout regressions; not evidence of model improvement."""
import copy
import sys
from pathlib import Path
import pytest
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from v17.universal_intelligence_paired_replay import compare_paired_shadow_forecasts

KEY = dict(sport="NBA", event_id="nba-game-1", market="POINTS",
           period="FULL_GAME", selection="player-1", line=20.5, direction="MORE")


def trial():
    incumbent = {
        **KEY, "event_start_utc": "2026-10-25T20:00:00Z",
        "forecast_asof_utc": "2026-10-25T19:00:00Z",
        "latest_feature_available_utc": "2026-10-25T18:45:00Z",
        "immutable_prediction_id": "inc-1", "model_artifact_id": "incumbent-v1",
        "artifact_checksum": "a" * 64,
        "training_cutoff_utc": "2026-10-20T00:00:00Z",
        "partition": "HOLDOUT", "calibrated_probability": .6,
        "calibrated_lower_bound": .51,
    }
    challenger = {**incumbent, "immutable_prediction_id": "ch-1",
                  "model_artifact_id": "challenger-v1", "artifact_checksum": "b" * 64,
                  "calibrated_probability": .7, "calibrated_lower_bound": .56}
    settlement = {
        **KEY, "event_start_utc": "2026-10-25T20:00:00Z",
        "settled_at_utc": "2026-10-26T01:00:00Z",
        "result_status": "FINAL", "void_or_push": False, "result": 1,
        "partition": "HOLDOUT",
    }
    return dict(incumbent=incumbent, challenger=challenger, settlement=settlement)


def run(row=None):
    return compare_paired_shadow_forecasts([trial() if row is None else row])


def test_valid_replay_unattested_and_metrics_correct():
    r = run()
    assert r["eligible_unattested"] == 1 and r["held"] == 0
    assert r["status"] == "NOT_ENOUGH_EVIDENCE"
    assert r["incumbent_brier_unattested"] == pytest.approx(.16)
    assert r["challenger_brier_unattested"] == pytest.approx(.09)
    assert r["brier_delta_challenger_minus_incumbent"] == pytest.approx(-.07)
    assert r["statistically_significant_superiority"] is None
    assert r["input_provenance_attested"] is False
    assert r["model_consumption_receipts"] == [] and r["can_execute"] is False


@pytest.mark.parametrize("record,key,value,reason", [
    ("challenger", "line", 19.5, "REPLAY_EXACT_IDENTITY_MISMATCH"),
    ("challenger", "direction", "LESS", "REPLAY_EXACT_IDENTITY_MISMATCH"),
    ("settlement", "selection", "player-2", "REPLAY_EXACT_IDENTITY_MISMATCH"),
    ("incumbent", "sport", "NFL", "REPLAY_EXACT_IDENTITY_MISMATCH"),
    ("challenger", "forecast_asof_utc", "2026-10-25T21:00:00Z",
     "REPLAY_TIME_LEAKAGE_OR_MISMATCH"),
    ("challenger", "latest_feature_available_utc", "2026-10-25T19:30:00Z",
     "REPLAY_TIME_LEAKAGE_OR_MISMATCH"),
    ("settlement", "settled_at_utc", "2026-10-25T19:59:00Z",
     "REPLAY_TIME_LEAKAGE_OR_MISMATCH"),
    ("incumbent", "partition", "TRAIN", "REPLAY_NOT_HOLDOUT"),
    ("incumbent", "immutable_prediction_id", "", "REPLAY_MISSING_IMMUTABLE_RECEIPT"),
    ("challenger", "immutable_prediction_id", "inc-1",
     "REPLAY_FORECAST_RECEIPTS_NOT_INDEPENDENT"),
    ("challenger", "model_artifact_id", "", "REPLAY_MODEL_PROVENANCE_MISSING"),
    ("challenger", "model_artifact_id", "incumbent-v1",
     "REPLAY_MODEL_ARTIFACT_NOT_DISTINCT"),
    ("incumbent", "artifact_checksum", "", "REPLAY_FITTED_CHECKSUM_MISSING_OR_INVALID"),
    ("incumbent", "artifact_checksum", "not-a-valid-checksum",
     "REPLAY_FITTED_CHECKSUM_MISSING_OR_INVALID"),
    ("challenger", "artifact_checksum", "a" * 64,
     "REPLAY_FITTED_ARTIFACT_BYTES_NOT_DISTINCT"),
    ("challenger", "training_cutoff_utc", "",
     "REPLAY_TRAINING_CUTOFF_UNVERIFIED"),
    ("challenger", "training_cutoff_utc", "2026-10-25T19:01:00Z",
     "REPLAY_TRAINING_CUTOFF_UNVERIFIED"),
    ("challenger", "calibrated_probability", float("nan"),
     "REPLAY_PROBABILITY_PACKAGE_INVALID"),
    ("challenger", "calibrated_lower_bound", .9, "REPLAY_PROBABILITY_PACKAGE_INVALID"),
    ("settlement", "result", 2, "REPLAY_SETTLEMENT_INVALID"),
    ("settlement", "void_or_push", True, "REPLAY_UNSETTLED_OR_VOID"),
    ("settlement", "result_status", "PENDING", "REPLAY_UNSETTLED_OR_VOID"),
])
def test_exact_line_direction_chronology_and_fail_closed(record, key, value, reason):
    t = trial()
    t[record][key] = value
    r = run(t)
    assert r["eligible_unattested"] == 0 and r["held"] == 1
    assert r["rows"][0]["status"] == reason
    assert r["incumbent_brier_unattested"] is None


def test_duplicate_thesis_does_not_double_count():
    t = trial()
    r = compare_paired_shadow_forecasts([t, copy.deepcopy(t)])
    assert r["eligible_unattested"] == r["held"] == 1
    assert r["rows"][1]["status"] == "REPLAY_DUPLICATE_THESIS"


def test_thirty_synthetic_rows_never_become_certified_superiority():
    rows = []
    for i in range(30):
        t = trial()
        for record in t.values():
            record["event_id"] = f"game-{i}"
        t["incumbent"]["immutable_prediction_id"] = f"inc-{i}"
        t["challenger"]["immutable_prediction_id"] = f"ch-{i}"
        rows.append(t)
    r = compare_paired_shadow_forecasts(rows)
    assert r["eligible_unattested"] == 30
    assert r["status"] == "DESCRIPTIVE_SHADOW_METRICS_NOT_ATTESTED"
    assert r["statistically_significant_superiority"] is None
    assert r["feature_ablation_completed"] is False
    assert r["probability_publishable"] is False


def test_empty_run_and_invalid_input():
    r = compare_paired_shadow_forecasts([])
    assert r["status"] == "NOT_ENOUGH_EVIDENCE" and r["received"] == 0
    assert r["brier_delta_challenger_minus_incumbent"] is None
    with pytest.raises(ValueError, match="PAIR_REPLAY_MINIMUM_N_INVALID"):
        compare_paired_shadow_forecasts([], minimum_n=1)
    with pytest.raises(ValueError, match="PAIR_REPLAY_INPUT_INVALID"):
        compare_paired_shadow_forecasts({})

def test_renamed_model_with_same_bytes_cannot_fake_challenger_improvement():
    t = trial()
    t["challenger"]["model_artifact_id"] = "new-uuid-new-version"
    t["challenger"]["artifact_checksum"] = t["incumbent"]["artifact_checksum"]
    t["challenger"]["calibrated_probability"] = .99
    r = compare_paired_shadow_forecasts([t])
    assert r["held"] == 1 and r["eligible_unattested"] == 0
    assert r["rows"][0]["status"] == "REPLAY_FITTED_ARTIFACT_BYTES_NOT_DISTINCT"
    assert r["brier_delta_challenger_minus_incumbent"] is None
    assert r["probability_publishable"] is False
    assert r["can_execute"] is False
