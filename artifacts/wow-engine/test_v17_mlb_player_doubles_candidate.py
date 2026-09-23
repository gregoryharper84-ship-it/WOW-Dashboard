from __future__ import annotations

from types import SimpleNamespace

import pytest

from v17.mlb_player_doubles_candidate import (
    ARTIFACT_SCHEMA_VERSION,
    MLBPlayerDoublesCandidateError,
    MODEL_FAMILY,
    score_candidate,
)
from v17.mlb_player_doubles_candidate_bridge import candidate_preflight


def _payload():
    return {
        "artifact_schema_version": ARTIFACT_SCHEMA_VERSION,
        "sport": "MLB",
        "stat_type": "PLAYER_DOUBLES",
        "model_family": MODEL_FAMILY,
        "supported_line": 0.5,
        "league_prior": 0.15,
        "player_probabilities": {"Bryce Harper": 0.23, "Alec Bohm": 0.22},
        "calibration_bins": [
            {"raw_mean": 0.10, "calibrated_probability": 0.11},
            {"raw_mean": 0.20, "calibrated_probability": 0.18},
            {"raw_mean": 0.30, "calibrated_probability": 0.27},
        ],
    }


def test_exact_half_line_scores_both_sides_as_research_only():
    more = score_candidate(_payload(), player="Bryce Harper", line=0.5, direction="MORE")
    less = score_candidate(_payload(), player="Bryce Harper", line=0.5, direction="LESS")
    assert more["candidate_family"] == "MLB_PLAYER_DOUBLES"
    assert more["candidate_raw_probability"] == pytest.approx(0.23)
    assert less["candidate_raw_probability"] == pytest.approx(0.77)
    assert more["candidate_historical_calibrated_probability"] + less["candidate_historical_calibrated_probability"] == pytest.approx(1.0)
    assert more["probability_publishable"] is False
    assert more["rank_eligible"] is False
    assert more["can_execute"] is False
    assert "MLB_PLAYER_DOUBLES_FORWARD_CALIBRATION_REQUIRED" in more["blockers"]


def test_unseen_player_uses_fitted_league_prior_not_market_probability():
    out = score_candidate(_payload(), player="Unseen Rookie", line=0.5, direction="MORE")
    assert out["candidate_raw_probability"] == pytest.approx(0.15)
    assert out["baseline_source"] == "LEAGUE_PRIOR_UNSEEN_PLAYER"
    assert out["historical_calibration_is_production_certification"] is False


def test_non_half_line_is_fail_closed_out_of_domain():
    with pytest.raises(MLBPlayerDoublesCandidateError) as exc:
        score_candidate(_payload(), player="Bryce Harper", line=1.5, direction="MORE")
    assert exc.value.code == "MLB_DOUBLES_LINE_OUT_OF_DOMAIN"


class _Query:
    def __init__(self, row):
        self.row = row

    def select(self, *_args, **_kwargs): return self
    def eq(self, *_args, **_kwargs): return self
    def limit(self, *_args, **_kwargs): return self
    def execute(self): return SimpleNamespace(data=[self.row])


class _DB:
    def __init__(self, row): self.row = row
    def table(self, _name): return _Query(self.row)


class _Prod:
    def __init__(self, row): self.row = row
    def get_client(self): return _DB(self.row)


class _MarketApi:
    def __init__(self, row): self.prod = _Prod(row)


def _artifact_row():
    return {
        "artifact_id": "candidate-1",
        "model_family": MODEL_FAMILY,
        "model_artifact_version": "MLB_PLAYER_DOUBLES_TEST_V1",
        "feature_schema_version": "PROP_FEATURES_V1",
        "specialist_version": "wow.mlb-player-doubles-expert@1.0.0",
        "lifecycle_state": "CANDIDATE",
        "candidate_research_active": True,
        "promoted": False,
        "active": False,
        "probability_publishable": False,
        "can_execute": False,
        "artifact_format": MODEL_FAMILY,
        "artifact_payload": _payload(),
    }


def test_preflight_converts_generic_absence_to_research_candidate_only():
    result = candidate_preflight(
        _MarketApi(_artifact_row()),
        "MLB",
        "PLAYER_DOUBLES",
        {"ok": False, "code": "MODEL_UNAVAILABLE"},
    )
    assert result is not None
    assert result["ok"] is True
    assert result["preflight_compatibility_mode"] == "MLB_PLAYER_DOUBLES_RESEARCH_EVIDENCE_ONLY"
    assert result["actual_artifact_lifecycle"] == "CANDIDATE"
    assert result["probability_publishable"] is False
    assert result["rank_eligible"] is False
    assert result["can_execute"] is False


def test_preflight_never_shadows_an_existing_production_route():
    result = candidate_preflight(
        _MarketApi(_artifact_row()),
        "MLB",
        "PLAYER_DOUBLES",
        {"ok": True, "code": "PROP_CERTIFIED_MODEL_ARTIFACT_READY"},
    )
    assert result is None
