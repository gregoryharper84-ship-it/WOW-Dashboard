"""Immutable NFL scorer to strict LLP package handoff without invented evidence."""
from __future__ import annotations

from copy import deepcopy
from types import SimpleNamespace

import pytest

from nfl_event_model_v17 import NFLModelOutputInvalid
from v17.llp_governed_package_scoring import PASS, validate_governed_scoring_package
from v17.nfl_team_event_publication import _attach_nfl_strict_scoring_package


@pytest.fixture
def req():
    return SimpleNamespace(
        official_event_id="2026_07_SF_ATL",
        home_team="Atlanta Falcons",
        away_team="San Francisco 49ers",
        source_snapshot_id="canonical-snapshot-1",
        latest_material_update_timestamp="2026-10-24T16:09:13+00:00",
    )


@pytest.fixture
def fitted():
    return {
        "canonical_event_id": "2026_07_SF_ATL",
        "event_prediction_id": "immutable-prediction-id",
        "score_snapshot_id": "immutable-score-id",
        "selected_participant": "San Francisco 49ers",
        "calibrated_home_probability": 0.1947979796387973,
        "calibrated_home_lower_bound": 0.13887989653022,
        "calibrated_home_upper_bound": 0.2507160627473746,
        "calibrated_away_probability": 0.8052020203612027,
        "calibrated_away_lower_bound": 0.7492839372526254,
        "calibrated_away_upper_bound": 0.86112010346978,
        "model_timestamp": "2026-10-25T11:30:33+00:00",
        "model_version": "NFL_EVENT_LOGREG_PLATT_V1",
        "calibration_method": "PLATT_TIME_SPLIT_V1",
        "calibration_version": "NFL_PLATT_TIME_SPLIT_V1",
    }


def _governed(fitted):
    return {
        **fitted,
        "code": "STAGE_PROOF_UNPROVEN",
        "probability_publishable": False,
        "rank_eligible": False,
        "terminal_label": "MODEL_QUALIFIED_HOLD",
        "global_terminal_authority": "V17_TERMINAL_REDUCER",
        "can_execute": False,
    }


def test_certified_away_selection_uses_immutable_persisted_side(req, fitted):
    gov = _governed(fitted)
    prior = deepcopy(gov)
    source = deepcopy(fitted)
    out = _attach_nfl_strict_scoring_package(gov, req, fitted)
    assert out["prediction_id"] == "immutable-prediction-id"
    assert out["calibrated_probability"] == fitted["calibrated_away_probability"]
    assert out["calibrated_lower_bound"] == fitted["calibrated_away_lower_bound"]
    assert out["calibrated_upper_bound"] == fitted["calibrated_away_upper_bound"]
    assert out["source_snapshot_id"] == req.source_snapshot_id
    assert out["source_snapshot_timestamp"] == req.latest_material_update_timestamp
    assert out["immutable_model_timestamp"] == fitted["model_timestamp"]
    assert out["outcome_space"] == "HOME_AWAY"
    assert validate_governed_scoring_package(out).status == PASS
    assert out["probability_publishable"] is False  # Stage proof remains absent
    assert out["rank_eligible"] is False
    assert out["terminal_label"] == "MODEL_QUALIFIED_HOLD"
    assert out["can_execute"] is False
    assert gov == prior and fitted == source


def test_certified_home_selection_is_not_reversed(req, fitted):
    candidate = {**fitted, "selected_participant": req.home_team}
    out = _attach_nfl_strict_scoring_package(_governed(candidate), req, candidate)
    assert out["calibrated_probability"] == fitted["calibrated_home_probability"]
    assert out["calibrated_lower_bound"] == fitted["calibrated_home_lower_bound"]
    assert out["calibrated_upper_bound"] == fitted["calibrated_home_upper_bound"]
    assert validate_governed_scoring_package(out).status == PASS


@pytest.mark.parametrize("change", [
    {"canonical_event_id": "2026_07_GB_DET"},
    {"event_prediction_id": ""},
    {"score_snapshot_id": ""},
    {"selected_participant": "Some Other Team"},
    {"calibrated_away_lower_bound": 0.90},  # Contradicts selected calibrated p
    {"model_timestamp": "2026-10-23T12:00:00+00:00"},  # Stale vs source
    {"calibrated_away_upper_bound": None},
])
def test_identity_or_calibration_contradiction_never_passes(req, fitted, change):
    candidate = {**fitted, **change}
    with pytest.raises(NFLModelOutputInvalid):
        _attach_nfl_strict_scoring_package(_governed(candidate), req, candidate)


def test_wrong_governor_score_snapshot_is_not_borrowed(req, fitted):
    gov = _governed(fitted)
    gov["score_snapshot_id"] = "another-model-run"
    with pytest.raises(NFLModelOutputInvalid, match="IDENTITY_OR_SIDE_INVALID"):
        _attach_nfl_strict_scoring_package(gov, req, fitted)


def test_missing_authoritative_source_timestamp_never_substituted(req, fitted):
    req.latest_material_update_timestamp = None
    with pytest.raises(NFLModelOutputInvalid, match="SOURCE_TIMESTAMP_MISSING"):
        _attach_nfl_strict_scoring_package(_governed(fitted), req, fitted)
