from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timedelta, timezone
import math

import pytest

from v17.ncaaf_current_event_result_form import (
    NCAAFForwardFeatureUnavailable,
    current_event_feature_package,
)
from v17.ncaaf_result_form_candidate import FEATURE_NAMES, FEATURE_SCHEMA_VERSION, MODEL_FAMILY


TARGET_START = "2026-10-09T23:00:00+00:00"


def _games():
    rows = []
    start = datetime(2026, 9, 1, 20, tzinfo=timezone.utc)
    for i in range(4):
        time = start + timedelta(days=7 * i)
        rows.append({
            "official_event_id": f"home-{i}",
            "event_start_time": time.isoformat(),
            "home_team": "Louisville",
            "away_team": f"Home Opponent {i}",
            "home_points": 28 + i,
            "away_points": 17,
            "home_won": True,
            "result_source": "CFBD:/games",
            "result_source_timestamp": (time + timedelta(hours=4)).isoformat(),
        })
        rows.append({
            "official_event_id": f"away-{i}",
            "event_start_time": time.isoformat(),
            "home_team": f"Away Opponent {i}",
            "away_team": "Florida State",
            "home_points": 10,
            "away_points": 14 + i,
            "home_won": False,
            "result_source": "CFBD:/games",
            "result_source_timestamp": (time + timedelta(hours=4)).isoformat(),
        })
    return rows


def _package(rows=None, **changes):
    options = {
        "official_event_id": "401858254",
        "event_start_time": TARGET_START,
        "home_team": "Louisville",
        "away_team": "Florida State",
        "neutral_site": False,
        "canonical_identity_verified": True,
        "canonical_resolution": {
            "event_id": "401858254",
            "event_start_time": TARGET_START,
            "home_team": "Louisville",
            "away_team": "Florida State",
            "identity_provider": "CFBD:/games",
            "identity_resolution": "CFBD_EXACT_PARTICIPANTS_START_MATCH",
            "market_features_used": False,
            "prediction_authority": False,
            "can_execute": False,
        },
    }
    options.update(changes)
    return current_event_feature_package(_games() if rows is None else rows, **options)


def test_forward_features_are_exact_candidate_schema_and_not_probabilities():
    result = _package()
    assert result["status"] == "RESEARCH_FORWARD_FEATURES_ONLY"
    assert result["official_event_id"] == "401858254"
    assert tuple(result["features"]) == FEATURE_NAMES
    assert result["feature_schema_version"] == FEATURE_SCHEMA_VERSION
    assert result["candidate_model_family"] == MODEL_FAMILY
    assert result["features"]["home_win_rate_prior"] == 1.0
    assert result["features"]["away_win_rate_prior"] == 1.0
    assert result["features"]["home_games_prior_log"] == pytest.approx(math.log1p(4))
    assert result["features"]["away_games_prior_log"] == pytest.approx(math.log1p(4))
    assert result["features"]["neutral_site"] == 0.0
    assert len(result["source_manifest"]["home_prior_event_ids"]) == 4
    assert len(result["source_manifest"]["away_prior_event_ids"]) == 4
    assert result["source_manifest"]["archived_pregame_snapshot"] is False
    assert result["source_manifest"]["market_features_used"] is False
    assert result["source_manifest_sha256"] == _package()["source_manifest_sha256"]
    assert result["calibrated_probability"] is None
    assert result["calibrated_lower_bound"] is None
    assert result["model_probability"] is None
    assert result["probability_publishable"] is False
    assert result["can_execute"] is False


def test_future_games_and_late_sources_do_not_change_current_features():
    original = _package()
    games = _games()
    games.append({
        "official_event_id": "late-source",
        "event_start_time": "2026-09-29T20:00:00+00:00",
        "home_team": "Louisville",
        "away_team": "Other",
        "home_points": 99,
        "away_points": 3,
        "home_won": True,
        "result_source": "CFBD:/games",
        "result_source_timestamp": "2026-10-10T00:00:00+00:00",
    })
    games.append({
        "official_event_id": "future-score",
        "event_start_time": "2026-10-10T21:00:00+00:00",
        "home_team": "Louisville",
        "away_team": "Other",
        "home_points": 99,
        "away_points": 3,
        "home_won": True,
        "result_source": "CFBD:/games",
        "result_source_timestamp": "2026-10-10T22:00:00+00:00",
    })
    result = _package(games)
    assert result["features"] == original["features"]
    assert result["source_manifest_sha256"] == original["source_manifest_sha256"]


def test_canonical_identity_is_required_from_upstream_canonical_resolver():
    with pytest.raises(NCAAFForwardFeatureUnavailable) as err:
        _package(canonical_identity_verified=False)
    assert err.value.code == "NCAAF_FORWARD_CANONICAL_IDENTITY_NOT_PROVEN"


def test_prior_form_minimum_blocks_and_never_imputes():
    with pytest.raises(NCAAFForwardFeatureUnavailable) as err:
        _package(_games()[:4])
    assert err.value.code == "NCAAF_FORWARD_PRIOR_FORM_INSUFFICIENT"


def test_duplicate_prior_event_is_rejected():
    rows = _games()
    rows.append(deepcopy(rows[0]))
    with pytest.raises(NCAAFForwardFeatureUnavailable) as err:
        _package(rows)
    assert err.value.code == "NCAAF_FORWARD_PRIOR_EVENT_DUPLICATE"


def test_target_outcome_never_enters_prior_history():
    rows = _games()
    rows.append({
        "official_event_id": "401858254",
        "home_team": "Louisville", "away_team": "Florida State",
        "event_start_time": TARGET_START,
    })
    with pytest.raises(NCAAFForwardFeatureUnavailable) as err:
        _package(rows)
    assert err.value.code == "NCAAF_FORWARD_TARGET_IN_HISTORY"


@pytest.mark.parametrize(
    "change,code",
    [
        ({"event_start_time": "2026-10-09T23:00:00"}, "NCAAF_FORWARD_EVENT_TIME_INVALID"),
        ({"neutral_site": "false"}, "NCAAF_FORWARD_NEUTRAL_SITE_INVALID"),
        ({"home_team": "Florida State"}, "NCAAF_FORWARD_EVENT_IDENTITY_INVALID"),
        ({"official_event_id": ""}, "NCAAF_FORWARD_EVENT_IDENTITY_INVALID"),
    ],
)
def test_bad_forward_event_input_fails_closed(change, code):
    with pytest.raises(NCAAFForwardFeatureUnavailable) as err:
        _package(**change)
    assert err.value.code == code


def test_prior_score_result_contradiction_is_blocked():
    rows = _games()
    rows[0]["home_won"] = False
    with pytest.raises(NCAAFForwardFeatureUnavailable) as err:
        _package(rows)
    assert err.value.code == "NCAAF_FORWARD_PRIOR_RESULT_CONTRADICTION"



def test_result_source_cannot_predate_prior_game():
    rows = _games()
    rows[0]["result_source_timestamp"] = "2026-08-31T20:00:00+00:00"
    with pytest.raises(NCAAFForwardFeatureUnavailable) as err:
        _package(rows)
    assert err.value.code == "NCAAF_FORWARD_PRIOR_EVIDENCE_TIME_CONTRADICTION"


@pytest.mark.parametrize("bad_score", [True, 14.5, "14.5"])
def test_prior_result_scores_must_be_real_whole_numbers(bad_score):
    rows = _games()
    rows[0]["home_points"] = bad_score
    with pytest.raises(NCAAFForwardFeatureUnavailable) as err:
        _package(rows)
    assert err.value.code == "NCAAF_FORWARD_PRIOR_RESULT_INVALID"


@pytest.mark.parametrize("invalid_proof", [
    {},
    {"event_id": "WRONG"},
    {"home_team": "Wrong School"},
    {"away_team": "Wrong School"},
    {"identity_provider": "ESPN"},
    {"identity_resolution": "UNVERIFIED_ALIAS"},
    {"market_features_used": True},
    {"prediction_authority": True},
    {"can_execute": True},
])
def test_canonical_cfbd_identity_proof_fields_must_match(invalid_proof):
    correct = {
        "event_id": "401858254",
        "event_start_time": TARGET_START,
        "home_team": "Louisville",
        "away_team": "Florida State",
        "identity_provider": "CFBD:/games",
        "identity_resolution": "CFBD_EXACT_PARTICIPANTS_START_MATCH",
        "market_features_used": False,
        "prediction_authority": False,
        "can_execute": False,
    }
    correct.update(invalid_proof)
    with pytest.raises(NCAAFForwardFeatureUnavailable) as err:
        _package(canonical_resolution=correct)
    assert err.value.code == "NCAAF_FORWARD_CANONICAL_PROOF_INVALID"


def test_canonical_event_start_tolerance_cannot_be_violated():
    resolution = {
        "event_id": "401858254",
        "event_start_time": "2026-10-09T21:59:00+00:00",
        "home_team": "Louisville",
        "away_team": "Florida State",
        "identity_provider": "CFBD:/games",
        "identity_resolution": "CFBD_EXACT_PARTICIPANTS_START_MATCH",
        "market_features_used": False,
        "prediction_authority": False,
        "can_execute": False,
    }
    with pytest.raises(NCAAFForwardFeatureUnavailable) as err:
        _package(canonical_resolution=resolution)
    assert err.value.code == "NCAAF_FORWARD_CANONICAL_START_MISMATCH"


def test_bare_verified_boolean_is_not_a_canonical_source_proof():
    with pytest.raises(NCAAFForwardFeatureUnavailable) as err:
        _package(canonical_resolution={})
    assert err.value.code == "NCAAF_FORWARD_CANONICAL_PROOF_INVALID"
