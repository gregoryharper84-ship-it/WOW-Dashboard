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
        "direction": "MORE", "line": 20.5, "market_type": "PLAYER_PROP",
        "probability_publishable": True,
        "calibrated_probability": .68, "calibrated_probability_lower_bound": .58,
    }
    o = {
        "prediction_id": "prop-1", "settlement_timestamp": "2026-11-21T22:00:00Z",
        "settlement_source": "official boxscore", "official_result": "HIT",
        "actual_stat": 23, "hit": True, "push": False, "void": False,
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


def test_exact_line_outcome_contradiction_is_never_learning_grade():
    p, o = prop()
    o["actual_stat"] = 19
    assert capture_eligibility(p, o, kind="PROP") == "BINARY_RESULT_CONFLICTS_WITH_EXACT_LINE"


def test_binary_result_may_not_conceal_push_or_void():
    p, o = prop()
    o["push"] = True
    assert capture_eligibility(p, o, kind="PROP") == "NONBINARY_SETTLEMENT_EXCLUDED_FROM_CALIBRATION"
    o["push"] = False
    o["void"] = True
    assert capture_eligibility(p, o, kind="PROP") == "NONBINARY_SETTLEMENT_EXCLUDED_FROM_CALIBRATION"
    o["void"] = False
    o["actual_stat"] = p["line"]
    assert capture_eligibility(p, o, kind="PROP") == "BINARY_RESULT_CONFLICTS_WITH_PUSH_LINE"


def test_hit_boolean_must_agree_with_realized_outcome():
    p, o = prop()
    o["hit"] = False
    assert capture_eligibility(p, o, kind="PROP") == "RECORDED_HIT_FLAG_CONFLICTS_WITH_RESULT"


def test_unrecognized_scalar_prop_market_cannot_borrow_player_prop_grade():
    p, o = prop()
    p["market_type"] = "FANTASY_SCORE_CANDIDATE"
    assert capture_eligibility(p, o, kind="PROP") == "SCALAR_PROP_MARKET_ROUTE_UNVERIFIED"


def test_directional_cohorts_never_pool_more_and_less():
    from v17.core_intelligence import build_learning_observation, cohort_key
    p, o = prop()
    a = {
        "prediction_id": p["prediction_id"],
        "sport": "NBA", "market_type": "PLAYER_PROP",
        "stat_type": "PTS", "direction": "MORE",
        "model_family": "NBA_POINTS", "model_artifact_version": "v1",
        "calibration_version": "c1", "calibrated_probability": .7,
    }
    outcome = {"prediction_id": p["prediction_id"], "official_result": "WIN",
               "settlement_source": "official", "settlement_timestamp": o["settlement_timestamp"]}
    more = build_learning_observation(a, outcome)
    less = build_learning_observation({**a, "direction": "LESS"}, outcome)
    assert cohort_key(more) != cohort_key(less)
    assert cohort_key(more).endswith("|MORE")
    assert cohort_key(less).endswith("|LESS")


def test_core_observation_does_not_drop_valid_zero_lower_bound():
    from v17.core_intelligence import build_learning_observation
    p, o = prop()
    record = {
        "prediction_id": p["prediction_id"], "sport": "NBA",
        "market_type": "PLAYER_PROP", "direction": "MORE",
        "model_family": "NBA_POINTS", "model_artifact_version": "v1",
        "calibration_version": "c1", "calibrated_probability": .72,
        "calibrated_probability_lower_bound": 0.0,
    }
    row = build_learning_observation(
        record, {"prediction_id": p["prediction_id"], "official_result": "WIN",
                 "settlement_source": "official", "settlement_timestamp": o["settlement_timestamp"]}
    )
    assert row.calibrated_lower_bound == 0.0


def test_event_role_ambiguous_cohort_cannot_start_calibration_challenger():
    from v17.core_intelligence import (
        build_learning_observation, summarize_cohort, detect_learning_hypotheses
    )
    rows = []
    for i in range(40):
        forecast = {
            "event_prediction_id": f"evt-{i}", "sport": "NHL", "league": "NHL",
            "market_family": "OUTRIGHT_WINNER", "direction": "HOME",
            "model_family": "NHL_MODEL", "model_version": "V1",
            "calibration_version": "c1", "calibrated_probability": .75,
            "calibrated_probability_lower_bound": .60,
        }
        outcome = {
            "event_prediction_id": f"evt-{i}", "official_result": "WIN" if i < 20 else "LOSS",
            "settlement_source": "official",
            "settlement_timestamp": "2026-11-21T23:00:00Z",
        }
        rows.append(build_learning_observation(forecast, outcome, source_prediction_kind="EVENT"))
    report = summarize_cohort(rows, min_samples=30)
    assert report.sample_n == 40
    assert report.ready_for_hypothesis is False
    assert detect_learning_hypotheses(report) == ()



def test_existing_mlb_team_aliases_match_official_winner_not_fuzzy_unrelated():
    p, o = event()
    p["sport"] = "MLB"
    p["home_team"] = "NYY"
    p["away_team"] = "BOS"
    o["official_winner"] = "New York Yankees"
    assert capture_eligibility(p, o, kind="EVENT") is None
    o["official_winner"] = "Boston Red Sox"
    assert capture_eligibility(p, o, kind="EVENT") == "OFFICIAL_EVENT_SCORE_WINNER_CONFLICT"


def test_soccer_canonical_out_right_winner_never_uses_binary_grader():
    p, o = event()
    p["sport"] = "SOCCER"
    p["market_family"] = "OUTRIGHT_WINNER"
    assert capture_eligibility(p, o, kind="EVENT") == "MULTICLASS_ROUTE_REQUIRED"


def test_nhl_unverified_aliases_do_not_gain_mlb_resolver_privileges():
    p, o = event()
    p["home_team"] = "NYY"
    o["official_winner"] = "New York Yankees"
    assert capture_eligibility(p, o, kind="EVENT") == "OFFICIAL_EVENT_SCORE_WINNER_CONFLICT"
