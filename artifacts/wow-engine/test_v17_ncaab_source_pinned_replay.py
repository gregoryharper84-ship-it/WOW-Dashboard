"""Negative and deterministic tests for independent source-pinned replay."""
from __future__ import annotations

from dataclasses import replace
import copy

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


def test_replay_receipt_records_redacted_numeric_runtime_and_component_digests():
    receipt = run()
    observed = receipt["observed"]
    runtime = observed["numeric_runtime"]
    assert runtime["python"] and runtime["numpy"] and runtime["scipy"]
    assert runtime["scikit_learn"]
    assert isinstance(runtime["threadpools"], list)
    assert "filepath" not in str(runtime) and "SUPABASE" not in str(runtime)
    assert observed["artifact_component_sha256"]["coefficients"] == _json_hash([0.1])
    assert all(len(value) == 64 for value in observed["artifact_component_sha256"].values())
    assert run()["receipt_sha256"] == receipt["receipt_sha256"]


def test_mismatch_records_numeric_evidence_without_weakening_gate():
    a, m, c, e = fixture()
    changed = replace(c, artifact_payload={"coefficients": [0.10000000000000002]})
    receipt = run(candidate=changed)
    assert receipt["status"] == "SOURCE_PINNED_REPLAY_MISMATCH_HOLD"
    assert "NCAAB_SOURCE_ARTIFACT_CHECKSUM_MISMATCH" in codes(receipt)
    assert receipt["observed"]["numeric_runtime"]["scikit_learn"]
    assert receipt["observed"]["artifact_component_sha256"]["coefficients"] != _json_hash([0.1])
    assert receipt["promotion"] is False and receipt["probability_publishable"] is False


def test_failure_diagnostic_only_exposes_allowlisted_numeric_evidence():
    from v17.ncaab_source_pinned_replay import failure_diagnostic
    a, m, c, e = fixture()
    changed = replace(c, artifact_payload={"coefficients": [0.7]})
    r = run(candidate=changed)
    r["mismatches"][0]["detail"] = "SECRET_PRIVATE_TOKEN_DO_NOT_LOG"
    r["unexpected_secret"] = "SECRET_PRIVATE_TOKEN_DO_NOT_LOG"
    diagnostic = failure_diagnostic(r)
    assert "NCAAB_SOURCE_ARTIFACT_CHECKSUM_MISMATCH" in diagnostic["mismatch_codes"]
    assert "SECRET_PRIVATE_TOKEN_DO_NOT_LOG" not in str(diagnostic)
    assert "coefficients" not in diagnostic
    assert diagnostic["numeric_runtime"]["scikit_learn"]
    assert diagnostic["certification"] is False and diagnostic["can_execute"] is False


def test_numeric_provenance_binds_native_libraries_without_leaking_paths():
    runtime = run()["observed"]["numeric_runtime"]
    assert runtime["joblib"] and runtime["threadpoolctl"]
    assert isinstance(runtime["numpy_enabled_simd"], list)
    assert all(flag.isidentifier() for flag in runtime["numpy_enabled_simd"])
    for library in runtime["threadpools"]:
        assert "filepath" not in library and "hostname" not in library
        if "binary_sha256" in library:
            digest = library["binary_sha256"]
            assert digest == "UNAVAILABLE" or (
                len(digest) == 64 and all(c in "0123456789abcdef" for c in digest)
            )
    assert "filepath" not in str(runtime)


def _synthetic_cpu_negative_control():
    """Build a signed synthetic receipt matching production-shaped evidence."""
    from v17 import ncaab_source_pinned_replay as replay
    a, m, c, e = fixture()
    fitted = replace(c, artifact_payload={"coefficients": [0.7]})
    receipt = run(candidate=fitted)
    observed = receipt["observed"]
    observed["dataset_hash"] = replay.EXPECTED_DATASET_HASH
    observed["calibrator_sha256"] = replay.EXPECTED_CALIBRATOR_SHA256
    observed["source_hashes"] = {
        str(k): value for k, value in replay.EXPECTED_SOURCE_HASHES.items()
    }
    observed["row_counts_by_season"] = {
        str(k): count for k, count in replay.EXPECTED_SOURCE_ROW_COUNTS.items()
    }
    observed["training_rows"] = sum(replay.EXPECTED_SOURCE_ROW_COUNTS.values())
    observed["metrics"] = dict(replay.EXPECTED_METRICS)
    receipt["expected"] = {
        "dataset_hash": replay.EXPECTED_DATASET_HASH,
        "artifact_checksum": replay.EXPECTED_ARTIFACT_CHECKSUM,
        "calibrator_sha256": replay.EXPECTED_CALIBRATOR_SHA256,
        "source_hashes": observed["source_hashes"],
    }
    receipt["mismatches"] = [{
        "code": "NCAAB_SOURCE_ARTIFACT_CHECKSUM_MISMATCH",
        "detail": observed["artifact_checksum"],
    }]
    receipt["receipt_sha256"] = _json_hash({
        key: value for key, value in receipt.items() if key != "receipt_sha256"
    })
    return receipt


def test_cpu_negative_control_is_verified_without_claiming_replay_pass():
    from v17.ncaab_source_pinned_replay import cpu_dispatch_negative_control_verified
    receipt = _synthetic_cpu_negative_control()
    assert receipt["status"] == "SOURCE_PINNED_REPLAY_MISMATCH_HOLD"
    assert cpu_dispatch_negative_control_verified(receipt, process_exit=1)
    assert not cpu_dispatch_negative_control_verified(receipt, process_exit=0)
    assert not cpu_dispatch_negative_control_verified(receipt, process_exit=3)
    assert receipt["certification"] is False
    assert receipt["probability_publishable"] is False


def test_cpu_negative_control_rejects_false_green_and_untrusted_receipts():
    from v17 import ncaab_source_pinned_replay as replay
    base = _synthetic_cpu_negative_control()
    for field, invalid in (
        ("status", "SOURCE_PINNED_REPLAY_REPRODUCED"),
        ("candidate_id", "another-candidate"),
        ("can_execute", True),
        ("probability_publishable", True),
        ("research_only", False),
    ):
        receipt = copy.deepcopy(base)
        receipt[field] = invalid
        receipt["receipt_sha256"] = _json_hash({
            k: v for k, v in receipt.items() if k != "receipt_sha256"
        })
        assert not replay.cpu_dispatch_negative_control_verified(receipt, process_exit=1), field

    for section, field, value in (
        ("observed", "dataset_hash", "0" * 64),
        ("observed", "calibrator_sha256", "0" * 64),
        ("observed", "source_hashes", {}),
        ("observed", "row_counts_by_season", {}),
        ("observed", "training_rows", 0),
        ("observed", "artifact_checksum", replay.EXPECTED_ARTIFACT_CHECKSUM),
        ("observed", "metrics", {}),
        ("expected", "dataset_hash", "0" * 64),
    ):
        receipt = copy.deepcopy(base)
        receipt[section][field] = value
        receipt["receipt_sha256"] = _json_hash({
            k: v for k, v in receipt.items() if k != "receipt_sha256"
        })
        assert not replay.cpu_dispatch_negative_control_verified(receipt, process_exit=1), field

    receipt = copy.deepcopy(base)
    receipt["mismatches"].append({"code": "NCAAB_SOURCE_ROW_PROVENANCE_MISMATCH", "detail": "unknown"})
    receipt["receipt_sha256"] = _json_hash({
        k: v for k, v in receipt.items() if k != "receipt_sha256"
    })
    assert not replay.cpu_dispatch_negative_control_verified(receipt, process_exit=1)

    altered = copy.deepcopy(base)
    altered["unexpected_untrusted_value"] = "tampered"
    assert not replay.cpu_dispatch_negative_control_verified(altered, process_exit=1)
