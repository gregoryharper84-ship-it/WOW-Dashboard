"""Capture integrity: only authentic frozen and eligible forecasts are gradeable.

These are synthetic fixtures, not proof of external source authentication.
"""
from __future__ import annotations

from copy import deepcopy
import pytest

from v17.learning_capture_guard import capture_eligibility


def prop():
    p = {
        "prediction_id": "prop-1", "created_at": "2026-11-21T17:00:00Z",
        "event_start_time": "2026-11-21T19:00:00Z",
        "model_timestamp": "2026-11-21T18:00:00Z",
        "locked_at": "2026-11-21T18:01:00Z",
        "source_snapshot_id": "source-1", "model_artifact_checksum": "sha256-test",
        "feature_snapshot_hash": "feature-test",
        "model_family": "NBA_PTS", "model_artifact_version": "v1",
        "direction": "MORE", "line": 20.5,
        "probability_publishable": True,
        "calibrated_probability": .68, "calibrated_probability_lower_bound": .58,
    }
    o = {
        "prediction_id": "prop-1", "settlement_timestamp": "2026-11-21T22:00:00Z",
        "settlement_source": "official boxscore", "official_result": "HIT",
    }
    return p, o


def event():
    p = {
        "event_prediction_id": "event-1", "created_at": "2026-11-21T17:00:00Z",
        "event_start_time": "2026-11-21T19:00:00Z",
        "model_timestamp": "2026-11-21T18:00:00Z",
        "latest_material_update_timestamp": "2026-11-21T17:59:00Z",
        "source_snapshot_id": "source-2", "model_artifact_id": "artifact-2",
        "controlling_specialist": "NHL_MODEL",
        "model_valid_after_latest_update": True,
        "calibration_status": "PASS",
        "market_family": "OUTRIGHT_WINNER",
        "home_team": "Home Name", "away_team": "Away Name",
        "probability_publishable": True,
        "calibrated_home_probability": .64, "calibrated_home_lower_bound": .54,
    }
    o = {
        "event_prediction_id": "event-1", "settlement_timestamp": "2026-11-21T22:00:00Z",
        "settlement_source": "official league", "official_winner": "Home Name",
        "home_score": 3, "away_score": 2, "void": False,
    }
    return p, o


@pytest.mark.parametrize("kind,factory", [("PROP", prop), ("EVENT", event)])
def test_valid_frozen_binary_package_eligible(kind, factory):
    p, o = factory()
    before, after = deepcopy(p), deepcopy(o)
    assert capture_eligibility(p, o, kind=kind) is None
    assert (p, o) == (before, after)


@pytest.mark.parametrize("kind,factory", [("PROP", prop), ("EVENT", event)])
def test_after_start_model_never_eligible(kind, factory):
    p, o = factory()
    p["model_timestamp"] = "2026-11-21T20:00:00Z"
    assert capture_eligibility(p, o, kind=kind) == "FROZEN_PREGAME_OR_SETTLEMENT_TIMING_UNVERIFIED"


@pytest.mark.parametrize("kind,factory", [("PROP", prop), ("EVENT", event)])
def test_missing_snapshot_and_late_settlement_fail_closed(kind, factory):
    p, o = factory()
    p["source_snapshot_id"] = None
    assert capture_eligibility(p, o, kind=kind) == "SOURCE_SNAPSHOT_ID_UNVERIFIED"
    p["source_snapshot_id"] = "source"
    o["settlement_timestamp"] = "2026-11-21T18:30:00Z"
    assert capture_eligibility(p, o, kind=kind) == "FROZEN_PREGAME_OR_SETTLEMENT_TIMING_UNVERIFIED"


@pytest.mark.parametrize("kind,factory", [("PROP", prop), ("EVENT", event)])
def test_unpublishable_cannot_grade_even_with_numeric_probability(kind, factory):
    p, o = factory()
    p["probability_publishable"] = False
    assert capture_eligibility(p, o, kind=kind) == "MODEL_NOT_PUBLISHABLE_FOR_CALIBRATION"


@pytest.mark.parametrize("kind,factory", [("PROP", prop), ("EVENT", event)])
def test_lower_bound_greater_than_point_blocked(kind, factory):
    p, o = factory()
    key = "calibrated_probability_lower_bound" if kind == "PROP" else "calibrated_home_lower_bound"
    p[key] = .99
    assert capture_eligibility(p, o, kind=kind) == "GOVERNED_CALIBRATION_PACKAGE_INVALID"


@pytest.mark.parametrize("result", ["VOID_PLAYER_DNP", "VOID_NOT_STARTING_PITCHER", "PUSH", "VOID"])
def test_propagated_void_suffix_never_scores_as_miss(result):
    p, o = prop()
    o["official_result"] = result
    assert capture_eligibility(p, o, kind="PROP") == "NONBINARY_SETTLEMENT_EXCLUDED_FROM_CALIBRATION"


def test_prop_requires_immutable_lock_before_start():
    p, o = prop()
    p["locked_at"] = None
    assert capture_eligibility(p, o, kind="PROP") == "IMMUTABLE_PREGAME_LOCK_UNVERIFIED"


def test_prop_raw_only_not_gradeable():
    p, o = prop()
    p["calibrated_probability"] = None
    p["raw_model_probability"] = .75
    assert capture_eligibility(p, o, kind="PROP") == "GOVERNED_CALIBRATION_PACKAGE_INVALID"


def test_feature_hash_can_substitute_optional_source_kind_label_not_snapshot():
    p, o = prop()
    p["evidence_source_kind"] = None
    assert capture_eligibility(p, o, kind="PROP") is None
    p["feature_snapshot_hash"] = None
    assert capture_eligibility(p, o, kind="PROP") == "PREGAME_SOURCE_SNAPSHOT_UNVERIFIED"


def test_event_1x2_requires_multiclass_grader():
    p, o = event()
    p["market_family"] = "SOCCER_1X2"
    assert capture_eligibility(p, o, kind="EVENT") == "MULTICLASS_ROUTE_REQUIRED"


def test_event_material_update_after_model_blocks():
    p, o = event()
    p["latest_material_update_timestamp"] = "2026-11-21T18:10:00Z"
    assert capture_eligibility(p, o, kind="EVENT") == "LATEST_MATERIAL_UPDATE_NOT_CONSUMED"


def test_event_draw_not_treated_as_home_loss():
    p, o = event()
    o["home_score"] = o["away_score"]
    assert capture_eligibility(p, o, kind="EVENT") == "NONBINARY_OR_UNVERIFIED_EVENT_SCORE"


def test_event_winner_must_match_final_score():
    p, o = event()
    o["official_winner"] = "Away Name"
    assert capture_eligibility(p, o, kind="EVENT") == "OFFICIAL_EVENT_SCORE_WINNER_CONFLICT"


def test_event_uncalibrated_artifact_not_included():
    p, o = event()
    p["calibration_status"] = None
    assert capture_eligibility(p, o, kind="EVENT") == "CALIBRATION_NOT_VERIFIED"


def test_prediction_receipt_identity_cannot_substitute():
    p, o = event()
    o["event_prediction_id"] = "another"
    assert capture_eligibility(p, o, kind="EVENT") == "SOURCE_PREDICTION_IDENTITY_UNRESOLVED"


def test_bool_and_nan_never_become_calibrated_probability():
    p, o = prop()
    for point in (True, float("nan"), float("inf"), -0.1):
        p["calibrated_probability"] = point
        assert capture_eligibility(p, o, kind="PROP") == "GOVERNED_CALIBRATION_PACKAGE_INVALID"


def test_source_receipt_fields_are_not_returned_as_secret_or_execution_authority():
    p, o = prop()
    assert capture_eligibility(p, o, kind="PROP") is None


@pytest.mark.parametrize("kind,factory", [("PROP", prop), ("EVENT", event)])
def test_zero_calibrated_lower_bound_is_valid_not_an_arbitrary_haircut(kind, factory):
    p, o = factory()
    key = "calibrated_probability_lower_bound" if kind == "PROP" else "calibrated_home_lower_bound"
    p[key] = 0.0
    assert capture_eligibility(p, o, kind=kind) is None
