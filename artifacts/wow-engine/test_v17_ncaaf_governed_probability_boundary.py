from types import SimpleNamespace

import pytest

import v17.ncaaf_team_event_publication as publication
from ncaaf_fitted_provider import NCAAFFittedProviderUnavailable
from team_event_request_runtime import TeamEventRequestRow, _completed_ncaaf
from v17.team_event_capability_manifest import (
    CERTIFIED_TEAM_EVENT_SPORTS,
    KNOWN_UNCERTIFIED_TEAM_EVENT_SPORTS,
    NFL_GAME_WIN_PROBABILITY_EXPERT,
)


class _Result:
    def __init__(self, data):
        self.data = data

    def execute(self):
        return self


class _NoModelDB:
    def rpc(self, name, params):
        assert name == "wow_ncaaf_certified_model_artifact"
        assert params == {"p_feature_schema_version": "NCAAF_FEATURES_V1"}
        return _Result({
            "ok": False,
            "code": "NCAAF_CERTIFIED_MODEL_ARTIFACT_NOT_FOUND",
            "probability_publishable": False,
            "can_execute": False,
        })


def _req():
    return SimpleNamespace(
        official_event_id="provider-event-1",
        event_start_time_utc="2099-09-14T18:00:00+00:00",
        home_team="Home State",
        away_team="Away State",
    )


def test_manifest_records_production_proven_nfl_but_not_ncaaf():
    assert CERTIFIED_TEAM_EVENT_SPORTS["NFL"] == NFL_GAME_WIN_PROBABILITY_EXPERT
    assert "NCAAF" not in CERTIFIED_TEAM_EVENT_SPORTS
    assert "NCAAF" in KNOWN_UNCERTIFIED_TEAM_EVENT_SPORTS


def test_ncaaf_missing_certified_artifact_stays_model_unavailable():
    with pytest.raises(NCAAFFittedProviderUnavailable) as exc:
        publication._score_static_probability(_req(), db=_NoModelDB())
    assert exc.value.code == "NCAAF_CERTIFIED_MODEL_ARTIFACT_NOT_FOUND"
    code, status = publication._normalize_provider_error(exc.value)
    assert code == "MODEL_UNAVAILABLE"
    assert status == 409


def test_ncaaf_completed_static_probability_preserves_probability_but_not_rank(monkeypatch):
    artifact = SimpleNamespace(
        model_family="NCAAF_LOGISTIC_V1",
        model_artifact_version="ncaaf-model-v1",
    )
    features = {
        "official_event_id": "cfbd-123",
        "feature_as_of": "2099-09-14T15:00:00+00:00",
        "event_start_time": "2099-09-14T18:00:00+00:00",
    }
    raw = SimpleNamespace(
        home_probability=0.62,
        away_probability=0.38,
        model_artifact_version="ncaaf-model-v1",
        artifact_id="artifact-1",
    )
    static = SimpleNamespace(
        calibrated_probability=0.60,
        calibration_lower_diagnostic=0.54,
        calibration_upper_diagnostic=0.66,
        calibrator_version="cal-1",
        calibration_method="EMPIRICAL_WILSON_BINS_V1",
        calibration_training_n=100,
        calibration_health_status="PASS",
    )

    monkeypatch.setattr(publication, "resolve_certified_artifact", lambda db, *, feature_schema_version: artifact)
    monkeypatch.setattr(publication, "_resolve_features", lambda req, *, db: ("cfbd-123", features, "TEAM_KICKOFF_CANONICAL_RESOLUTION"))
    monkeypatch.setattr(publication, "infer_raw_probability", lambda db, *, request, features: raw)
    monkeypatch.setattr(publication, "calibrate_from_registry", lambda db, *, model_artifact_version, raw_probability: static)

    result = publication._score_static_probability(_req(), db=object())
    assert result["sporting_probability_completed"] is True
    assert result["selected_participant"] == "Home State"
    assert result["calibrated_selection_probability"] == pytest.approx(0.60)
    assert result["static_calibration_home_lower_diagnostic"] == pytest.approx(0.54)
    assert result["calibrated_lower_bound"] is None
    assert result["calibrated_upper_bound"] is None
    assert result["code"] == publication.FINAL_BOUND_BLOCKER
    assert result["rank_eligible"] is False
    assert result["probability_publishable"] is False
    assert result["can_execute"] is False


def test_objective_completion_keeps_sporting_probability_when_publication_is_blocked():
    row = TeamEventRequestRow(
        research_run_id="run-1",
        objective_lane="OUTRIGHT_WIN_PROBABILITY",
        sport="NCAAF",
        league="NCAAF",
        event_key="NCAAF:provider-event-1",
        event_state="PREGAME",
        event_date="2099-09-14",
        timezone="America/Chicago",
        price_required_for_objective=False,
        event_start_time_utc="2099-09-14T18:00:00+00:00",
        home_team="Home State",
        away_team="Away State",
    )
    scored = {
        "sporting_probability_completed": True,
        "selected_participant": "Home State",
        "calibrated_selection_probability": 0.60,
        "code": publication.FINAL_BOUND_BLOCKER,
        "terminal_label": "MODEL_QUALIFIED_HOLD",
        "blockers": [publication.FINAL_BOUND_BLOCKER],
        "model_artifact_version": "ncaaf-model-v1",
        "calibration_version": "cal-1",
        "probability_publishable": False,
        "rank_eligible": False,
        "can_execute": False,
    }
    result = _completed_ncaaf(row, scored)
    assert result["terminal_status"] == "COMPLETED"
    assert result["code"] == "SPORTING_PROBABILITY_COMPLETED"
    assert result["calibrated_probability"] == pytest.approx(0.60)
    assert result["calibrated_lower_bound"] is None
    assert result["probability_publishable"] is False
    assert result["rank_eligible"] is False
    assert publication.FINAL_BOUND_BLOCKER in result["blockers"]
    assert result["can_execute"] is False
