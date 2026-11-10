"""Regression tests: reconstructed features remain non-certified."""

import pytest
from pregame_provenance_gate import audit_d1_provenance, audit_snapshot_alignment


def row(number=0):
    return {
        "training_row_id": "d"+str(number), "sport": "MLB",
        "model_family": "MLB_DYNAMIC_TEAM_STATE_LOGIT_V2",
        "feature_schema_version": "MLB_DYNAMIC_TEAM_STATE_FEATURES_V2",
        "official_event_id": "MLB:823164",
        "event_start_time": "2026-09-27T19:05:00Z",
        "feature_as_of": "2026-09-27T19:04:59Z",
        "historical_reconstruction": True,
        "archived_pregame_snapshot": False,
        "source_manifest_sha256": "a"*64,
        "features": {"home_win_rate_l10": 0.55},
        "outcome_json": {"home_win": False},
    }


def source(capture="2026-09-27T19:04:59Z"):
    return {
        "official_event_id": "823164",
        "event_start_time": "2026-09-27T19:05:00Z",
        "model_timestamp": "2026-09-27T19:02:00Z",
        "source_captured_at": capture,
    }


def test_reconstruction_never_self_certifies():
    report = audit_d1_provenance([row()])
    assert report["validated_rows"] == 1
    assert report["independently_certified_feature_groups"] == 0
    assert report["reasons"] == {"HISTORICAL_RECONSTRUCTION_NOT_ARCHIVED": 1}
    assert report["can_execute"] is False


def test_archived_flag_alone_cannot_certify():
    data = row()
    data["historical_reconstruction"] = False
    data["archived_pregame_snapshot"] = True
    report = audit_d1_provenance([data])
    assert report["self_declared_archived_groups"] == 1
    assert report["independently_certified_feature_groups"] == 0


def test_repeated_outcome_not_independent_game():
    report = audit_d1_provenance([row(0), row(1)])
    assert report["unique_model_event_groups"] == 1
    assert report["repeated_model_event_rows"] == 1
    assert report["ambiguous_feature_or_outcome_groups"] == 0


def test_ambiguous_repeated_features_flagged():
    a, b = row(0), row(1)
    b["features"]["home_win_rate_l10"] = 0.11
    assert audit_d1_provenance([a, b])["ambiguous_feature_or_outcome_groups"] == 1


@pytest.mark.parametrize("field,value,reason", [
    ("feature_as_of", "2026-09-27T19:05:00Z", "FEATURE_ASOF_NOT_PREGAME"),
    ("source_manifest_sha256", "not-hash", "SOURCE_MANIFEST_HASH_MISSING_OR_INVALID"),
    ("outcome_json", {"home_win": "yes"}, "BINARY_OUTCOME_MISSING"),
    ("features", {"x": float("nan")}, "FEATURE_NON_FINITE_OR_NONNUMERIC"),
    ("event_start_time", "2026-09-27T19:05:00", "TIMESTAMP_NOT_TIMEZONE_AWARE"),
])
def test_invalid_rows_rejected(field, value, reason):
    d = row()
    d[field] = value
    r = audit_d1_provenance([d])
    assert r["validated_rows"] == 0
    assert r["reasons"] == {reason: 1}


def test_source_after_model_not_model_input_proof():
    report = audit_snapshot_alignment([source()])
    assert report["reason_counts"] == {"SOURCE_CAPTURE_AFTER_MODEL": 1}
    assert report["certified_feature_snapshots"] == 0


def test_source_before_model_still_unverified_feature_match():
    report = audit_snapshot_alignment([source("2026-09-27T19:00:00Z")])
    assert report["reason_counts"] == {"SOURCE_CAPTURE_BEFORE_MODEL_UNVERIFIED_FEATURE_MATCH": 1}
    assert report["certified_feature_snapshots"] == 0


def test_source_after_event_rejected():
    report = audit_snapshot_alignment([source("2026-09-27T20:00:00Z")])
    assert report["reason_counts"] == {"SOURCE_CAPTURE_NOT_PREGAME": 1}


def test_empty_data_cannot_certify():
    assert audit_d1_provenance([])["independently_certified_feature_groups"] == 0
    assert audit_snapshot_alignment([])["certified_feature_snapshots"] == 0
