"""Negative and deterministic tests for independent source-pinned replay."""
from __future__ import annotations

from dataclasses import replace

from v17.binary_candidate_lifecycle import BinaryCandidate, BinaryCandidateMetrics
from v17.ncaab_sportsdataverse_candidate import FEATURE_NAMES, MODEL_FAMILY, _json_hash
from v17.ncaab_source_pinned_replay import verify_public_source

SOURCE = "a" * 64
DATASET = "c" * 64


def fixture():
    asset = [{
        "season": 2026, "sha256": SOURCE, "license": "CC-BY-4.0",
        "url": "https://github.com/sportsdataverse/sportsdataverse-data/releases/download/"
               "espn_mens_college_basketball_team_boxscores/team_box_2026.csv",
    }]
    meta = [
        {"source_manifest": {"source_sha256": SOURCE, "game_id": str(i),
                             "market_features_used": False}} for i in range(2)
    ]
    metrics = BinaryCandidateMetrics(
        train_n=1, calibration_n=1, test_n=1, raw_brier=0.2,
        calibrated_brier=0.1, baseline_brier=0.3,
        raw_log_loss=0.4, calibrated_log_loss=0.3,
        baseline_log_loss=0.5, ece=0.02,
    )
    fitted = BinaryCandidate(
        model_family=MODEL_FAMILY, feature_names=FEATURE_NAMES,
        artifact_payload={"coefficients": [0.1]},
        calibrator_payload={"method": "IDENTITY"},
        metrics=metrics, dataset_hash=DATASET, calibration_start_event="A",
        calibration_end_event="B", test_start_event="C", test_end_event="D",
        research_screen_pass=True,
    )
    expected = {
        "expected_hashes": {2026: SOURCE},
        "expected_counts": {2026: 2},
        "expected_dataset_hash": DATASET,
        "expected_artifact_checksum": _json_hash(dict(fitted.artifact_payload)),
        "expected_calibrator_sha256": _json_hash(dict(fitted.calibrator_payload)),
        "expected_metrics": {"train_n": 1, "calibration_n": 1, "test_n": 1, "ece": 0.02,
                             "calibrated_brier": 0.1},
    }
    return asset, meta, fitted, expected


def run(asset=None, metadata=None, candidate=None, expected=None):
    a, m, c, e = fixture()
    return verify_public_source(a if asset is None else asset,
                                m if metadata is None else metadata,
                                c if candidate is None else candidate,
                                **(e if expected is None else expected))


def codes(receipt):
    return {m["code"] for m in receipt["mismatches"]}


def test_exact_source_challenger_never_certifies_or_publishes():
    receipt = run()
    assert receipt["status"] == "SOURCE_PINNED_REPLAY_REPRODUCED", receipt["mismatches"]
    assert receipt["source_replay_not_db_frozen_replay"] is True
    assert receipt["can_execute"] is False and receipt["probability_publishable"] is False
    assert receipt["certification"] is False and receipt["promotion"] is False
    assert len(receipt["receipt_sha256"]) == 64
    assert run()["receipt_sha256"] == receipt["receipt_sha256"]


def test_mismatched_sha256_is_typed_hold():
    a, m, c, e = fixture()
    a[0]["sha256"] = "b" * 64
    r = run(asset=a)
    assert r["status"] == "SOURCE_PINNED_REPLAY_MISMATCH_HOLD"
    assert "NCAAB_SOURCE_PIN_MISMATCH" in codes(r)


def test_missing_source_and_duplicate_season_are_typed_holds():
    a, m, c, e = fixture()
    assert "NCAAB_SOURCE_SEASON_MISSING" in codes(run(asset=[]))
    assert "NCAAB_SOURCE_DUPLICATE_SEASON" in codes(run(asset=a + a))


def test_unexpected_source_row_count_is_hold():
    a, m, c, e = fixture()
    assert "NCAAB_SOURCE_ROW_COUNT_MISMATCH" in codes(run(metadata=m[:1]))


def test_manifest_market_flag_and_wrong_source_are_holds():
    a, m, c, e = fixture()
    m[1]["source_manifest"]["market_features_used"] = True
    assert "NCAAB_SOURCE_ROW_PROVENANCE_MISMATCH" in codes(run(metadata=m))
    a, m, c, e = fixture()
    m[1]["source_manifest"]["source_sha256"] = "e" * 64
    assert "NCAAB_SOURCE_ROW_PROVENANCE_MISMATCH" in codes(run(metadata=m))


def test_artifact_or_calibrator_changes_held():
    a, m, c, e = fixture()
    wrong = replace(c, artifact_payload={"coefficients": [0.7]})
    assert "NCAAB_SOURCE_ARTIFACT_CHECKSUM_MISMATCH" in codes(run(candidate=wrong))
    wrong = replace(c, calibrator_payload={"method": "EMPIRICAL"})
    assert "NCAAB_SOURCE_CALIBRATOR_CHECKSUM_MISMATCH" in codes(run(candidate=wrong))


def test_specialist_data_hash_and_metrics_changes_held():
    a, m, c, e = fixture()
    assert "NCAAB_SOURCE_SPECIALIST_MISMATCH" in codes(run(candidate=replace(c, model_family="NCAAF")))
    assert "NCAAB_SOURCE_DATASET_HASH_MISMATCH" in codes(run(candidate=replace(c, dataset_hash="0" * 64)))
    wrong_metrics = replace(c.metrics, ece=0.5)
    assert "NCAAB_SOURCE_METRIC_MISMATCH" in codes(run(candidate=replace(c, metrics=wrong_metrics)))


def test_governance_change_always_holds():
    a, m, c, e = fixture()
    for field in ("can_execute", "probability_publishable", "automatic_certification", "automatic_promotion"):
        assert "NCAAB_SOURCE_GOVERNANCE_VIOLATION" in codes(run(candidate=replace(c, **{field: True})))
