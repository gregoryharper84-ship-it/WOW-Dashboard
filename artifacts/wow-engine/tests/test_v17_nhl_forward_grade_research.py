"""NHL official settlement forward grade, entirely synthetic fixtures."""
from datetime import datetime, timedelta, timezone

import pytest

from v17.nhl_forward_grade_research import (
    NHLForwardGradeError, grade_nhl_pregame_forecast,
    summarize_nhl_forward_grade_cohort,
)
from v17.nhl_provenance import (
    SOURCE_PAYLOAD_HASH_SCOPE, SOURCE_PROVENANCE_VERSION,
    provenance_envelope_hash,
)

START = datetime(2026, 11, 23, 0, tzinfo=timezone.utc)


def forecast(**overrides):
    row = {
        "official_event_id": "2026020381",
        "immutable_prediction_id": "synthetic-immutable-prediction-1",
        "forecast_created_at_utc": (START - timedelta(hours=2)).isoformat(),
        "fitted_model_version": "synthetic-nhl-fitted-1",
        "model_artifact_checksum": "a" * 64,
        "feature_snapshot_hash": "b" * 64,
        "source_snapshot_id": "synthetic-trusted-source-id",
        "selected_participant": "New York Islanders",
        "calibrated_probability": 0.60,
        "calibrated_lower_bound": 0.52,
        "can_execute": False,
    }
    row.update(overrides)
    return row


def settled(**overrides):
    y = {
        "home_score": 4, "away_score": 3,
        "provenance_version": SOURCE_PROVENANCE_VERSION,
        "game_type": 2, "game_state": "FINAL",
        "neutral_site": False,
        "payload_hash_scope": SOURCE_PAYLOAD_HASH_SCOPE,
        "source_payload_commitment": "f" * 64,
    }
    event = {
        "official_event_id": "2026020381", "sport": "NHL",
        "season": "20262027", "event_start_time": START.isoformat(),
        "home_participant": "NYI", "away_participant": "TOR",
        "source_provider": "NHL_PUBLIC_WEB_API",
        "source_uri": "https://api-web.nhle.com/v1/club-schedule-season/NYI/20262027",
        "source_retrieved_at": (START + timedelta(hours=4)).isoformat(),
        "outcome_json": y,
    }
    event["source_payload_sha256"] = provenance_envelope_hash(
        official_event_id=event["official_event_id"], season=event["season"],
        event_start_time=event["event_start_time"],
        home_team="NYI", away_team="TOR",
        home_score=4, away_score=3, game_type=2,
        game_state="FINAL", neutral_site=False,
    )
    event.update(overrides)
    return event


def test_exact_official_forward_grade_and_cohort_remain_research_only():
    row = grade_nhl_pregame_forecast(forecast(), settled())
    assert row["outcome"] == 1
    assert row["brier"] == pytest.approx(0.16)
    assert row["log_loss"] == pytest.approx(0.5108256238)
    assert row["calibrated_lower_bound"] == .52
    assert row["can_execute"] is False
    report = summarize_nhl_forward_grade_cohort([row])
    assert report["n"] == 1
    assert report["fitted_model_version"] == "synthetic-nhl-fitted-1"
    assert report["sample_warning"] == "INSUFFICIENT_FORWARD_COHORT"
    assert report["certification_eligible"] is False


@pytest.mark.parametrize("change,code", [
    ({"forecast_created_at_utc": START.isoformat()}, "NHL_GRADE_CHRONOLOGY_INVALID"),
    ({"forecast_created_at_utc": (START + timedelta(minutes=2)).isoformat()},
     "NHL_GRADE_CHRONOLOGY_INVALID"),
    ({"immutable_prediction_id": ""}, "NHL_FORECAST_RECEIPT_PROVENANCE_INCOMPLETE"),
    ({"source_snapshot_id": ""}, "NHL_FORECAST_RECEIPT_PROVENANCE_INCOMPLETE"),
    ({"model_artifact_checksum": "a" * 63}, "NHL_FORECAST_RECEIPT_PROVENANCE_INCOMPLETE"),
    ({"calibrated_lower_bound": .9}, "NHL_FORECAST_CALIBRATION_INVALID"),
    ({"selected_participant": "Boston Bruins"}, "NHL_GRADE_SELECTED_SIDE_UNVERIFIED"),
    ({"can_execute": True}, "NHL_FORECAST_EXECUTION_GOVERNANCE_INVALID"),
])
def test_retrospective_malformed_or_unknown_forecasts_hold(change, code):
    with pytest.raises(NHLForwardGradeError) as exc:
        grade_nhl_pregame_forecast(forecast(**change), settled())
    assert exc.value.code == code


@pytest.mark.parametrize("change,code", [
    ({"official_event_id": "2026020376"}, "NHL_GRADE_EVENT_IDENTITY_MISMATCH"),
    ({"source_provider": "ESPN"}, "NHL_SETTLEMENT_PROVENANCE_INVALID"),
    ({"source_payload_sha256": "a" * 64}, "NHL_SETTLEMENT_ENVELOPE_HASH_MISMATCH"),
    ({"source_retrieved_at": (START - timedelta(hours=1)).isoformat()},
     "NHL_GRADE_CHRONOLOGY_INVALID"),
    ({"outcome_json": {"home_score": 4, "away_score": 3}},
     "NHL_SETTLEMENT_PROVENANCE_INVALID"),
])
def test_untrusted_settlement_or_forged_source_fails(change, code):
    with pytest.raises(NHLForwardGradeError) as exc:
        grade_nhl_pregame_forecast(forecast(), settled(**change))
    assert exc.value.code == code


def test_official_away_selection_and_final_to_off_same_result():
    source = settled()
    source["outcome_json"]["game_state"] = "OFF"
    grade = grade_nhl_pregame_forecast(
        forecast(selected_participant="Toronto Maple Leafs"), source,
    )
    assert grade["outcome"] == 0
    assert grade["brier"] == pytest.approx(.36)


def test_model_versions_and_event_duplicates_never_mingle():
    row = grade_nhl_pregame_forecast(forecast(), settled())
    other = dict(row, fitted_model_version="another-model")
    with pytest.raises(NHLForwardGradeError) as exc:
        summarize_nhl_forward_grade_cohort([row, other])
    assert exc.value.code == "NHL_GRADE_MODEL_VERSION_MIXED"
    duplicate = dict(row, immutable_prediction_id="a-second-copy")
    with pytest.raises(NHLForwardGradeError) as exc:
        summarize_nhl_forward_grade_cohort([row, duplicate])
    assert exc.value.code == "NHL_GRADE_DUPLICATE_EVENT"
    assert summarize_nhl_forward_grade_cohort([])["n"] == 0
