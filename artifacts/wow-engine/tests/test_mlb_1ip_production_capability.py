from datetime import datetime, timedelta, timezone

import pytest

from mlb_1ip_artifact_pipeline import TrainingRow, fit_candidate, validate_candidate
from mlb_1ip_final_refresh import refresh_queue_row


def test_artifact_refuses_small_training_sample():
    with pytest.raises(ValueError, match="MLB_1IP_TRAINING_ROWS_INSUFFICIENT"):
        fit_candidate([TrainingRow(bf=3, pitches=12)] * 999, training_code_sha="a" * 40)


def test_artifact_certification_requires_empirical_validation_gates():
    candidate = fit_candidate([TrainingRow(bf=3, pitches=12)] * 1000, training_code_sha="a" * 40)
    assert candidate["lifecycle_state"] == "CANDIDATE"
    assert candidate["promoted"] is False
    assert candidate["active"] is False
    assert candidate["probability_publishable"] is False
    assert candidate["can_execute"] is False

    y = [0, 1] * 125
    p = [0.49, 0.51] * 125
    certified = validate_candidate(candidate, y, p)
    assert certified["lifecycle_state"] == "PROSPECTIVE_CERTIFIED"
    assert certified["promoted"] is True
    assert certified["active"] is True
    assert certified["probability_publishable"] is False
    assert certified["can_execute"] is False


def test_refresh_waits_until_official_lineup():
    now = datetime.now(timezone.utc)
    row = {
        "queue_id": "q1",
        "event_start_time": (now + timedelta(hours=2)).isoformat(),
        "player": "Pitcher",
        "starter_name_at_capture": "Pitcher",
    }

    def fake(**kwargs):
        return {"starter_name": "Pitcher", "official_lineup_status": "TBD", "can_execute": False}

    result = refresh_queue_row(row, hydrator=fake, now=now)
    assert result["status"] == "WAITING_FOR_OFFICIAL_LINEUP"
    assert result["rerun_required"] is False
    assert result["can_execute"] is False


def test_refresh_purges_only_changed_starter_row():
    now = datetime.now(timezone.utc)
    row = {
        "queue_id": "q2",
        "event_start_time": (now + timedelta(hours=2)).isoformat(),
        "player": "Old Pitcher",
        "starter_name_at_capture": "Old Pitcher",
    }

    def fake(**kwargs):
        return {"starter_name": "New Pitcher", "official_lineup_status": "CONFIRMED", "can_execute": False}

    result = refresh_queue_row(row, hydrator=fake, now=now)
    assert result["status"] == "SLATE_PURGE"
    assert result["terminal_label"] == "SLATE_PURGE"
    assert result["can_execute"] is False


def test_refresh_ready_to_rerun_when_lineup_confirms():
    now = datetime.now(timezone.utc)
    row = {
        "queue_id": "q3",
        "event_start_time": (now + timedelta(hours=2)).isoformat(),
        "player": "Pitcher",
        "starter_name_at_capture": "Pitcher",
    }

    def fake(**kwargs):
        return {"starter_name": "Pitcher", "official_lineup_status": "CONFIRMED", "projected_top_four": [{"player": "A"}], "can_execute": False}

    result = refresh_queue_row(row, hydrator=fake, now=now)
    assert result["status"] == "READY_TO_RERUN"
    assert result["rerun_required"] is True
    assert result["probability_publishable"] is False
    assert result["can_execute"] is False
