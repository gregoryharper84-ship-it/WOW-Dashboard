from datetime import datetime, timedelta, timezone

import pytest

from mlb_1ip_artifact_pipeline import (
    TrainingRow,
    fit_candidate,
    promote_validated_candidate,
    validate_candidate,
)
from mlb_1ip_empirical_pmf import fit_empirical_pmf
from mlb_1ip_final_refresh import refresh_queue_row
from mlb_1ip_final_refresh_job import _rerun
from mlb_1ip_training_dataset import game_training_rows


def _lineage_kwargs():
    return {
        "scoring_code_sha": "b" * 40,
        "split_hash": "c" * 64,
        "source_snapshot_hashes": ["d" * 64],
    }


def _passing_validation_fixture():
    return [0, 1] * 125, [0.01, 0.99] * 125


def _empirical_artifact():
    rows = []
    rows.extend(TrainingRow(bf=3, pitches=12 + i % 4) for i in range(450))
    rows.extend(TrainingRow(bf=4, pitches=16 + i % 5) for i in range(400))
    rows.extend(TrainingRow(bf=5, pitches=21 + i % 6) for i in range(300))
    payload = fit_empirical_pmf(rows)
    return {
        "ok": True,
        "code": "PROP_CERTIFIED_MODEL_ARTIFACT_READY",
        "model_family": payload["model_family"],
        "model_artifact_version": "MLB_1IP_TEST_ARTIFACT_V1",
        "artifact_checksum": payload["artifact_checksum"],
        "certification_id": "PROP-CERT-TEST-MLB-1IP",
        "artifact_payload": payload,
        "supported_line_min": 11.5,
        "supported_line_max": 21.5,
        "feature_schema_version": "PROP_FEATURES_V1",
        "probability_publishable": False,
        "can_execute": False,
    }


def test_artifact_refuses_small_training_sample():
    with pytest.raises(ValueError, match="MLB_1IP_TRAINING_ROWS_INSUFFICIENT"):
        fit_candidate([TrainingRow(bf=3, pitches=12)] * 999, training_code_sha="a" * 40)


def test_validation_cannot_self_promote_artifact():
    candidate = fit_candidate([TrainingRow(bf=3, pitches=12)] * 1000, training_code_sha="a" * 40)
    y, p = _passing_validation_fixture()
    validated = validate_candidate(candidate, y, p, **_lineage_kwargs())
    assert validated["validation_metrics"]["gates_passed"] is True
    assert validated["validation_lineage"]["artifact_checksum"] == candidate["artifact_checksum"]
    assert validated["lifecycle_state"] == "SHADOW"
    assert validated["certification_id"] is None
    assert validated["promoted"] is False
    assert validated["active"] is False
    assert validated["probability_publishable"] is False
    assert validated["can_execute"] is False


def test_promotion_requires_independent_review_context():
    candidate = fit_candidate([TrainingRow(bf=3, pitches=12)] * 1000, training_code_sha="a" * 40)
    y, p = _passing_validation_fixture()
    validated = validate_candidate(candidate, y, p, **_lineage_kwargs())
    with pytest.raises(ValueError, match="MLB_1IP_INDEPENDENT_REVIEW_REQUIRED"):
        promote_validated_candidate(
            validated,
            implementer_context="session-a",
            reviewer_context="session-a",
            review_verdict="APPROVE_FOR_PROMOTION",
            review_evidence_hash="e" * 64,
        )
    promoted = promote_validated_candidate(
        validated,
        implementer_context="session-a",
        reviewer_context="session-b",
        review_verdict="APPROVE_FOR_PROMOTION",
        review_evidence_hash="e" * 64,
    )
    assert promoted["lifecycle_state"] == "PROSPECTIVE_CERTIFIED"
    assert promoted["promoted"] is True
    assert promoted["active"] is True
    assert promoted["probability_publishable"] is False
    assert promoted["can_execute"] is False


def test_training_dataset_excludes_first_inning_reliever():
    def fake_get(url, params=None, timeout=None):
        class Response:
            def raise_for_status(self):
                return None
            def json(self):
                return {"allPlays": [
                    {"about": {"inning": 1, "halfInning": "top"}, "matchup": {"pitcher": {"id": 10}}, "playEvents": [{"isPitch": True}] * 4},
                    {"about": {"inning": 1, "halfInning": "top"}, "matchup": {"pitcher": {"id": 10}}, "playEvents": [{"isPitch": True}] * 5},
                    {"about": {"inning": 1, "halfInning": "top"}, "matchup": {"pitcher": {"id": 10}}, "playEvents": [{"isPitch": True}] * 4},
                    {"about": {"inning": 1, "halfInning": "top"}, "matchup": {"pitcher": {"id": 99}}, "playEvents": [{"isPitch": True}] * 6},
                    {"about": {"inning": 1, "halfInning": "bottom"}, "matchup": {"pitcher": {"id": 20}}, "playEvents": [{"isPitch": True}] * 4},
                    {"about": {"inning": 1, "halfInning": "bottom"}, "matchup": {"pitcher": {"id": 20}}, "playEvents": [{"isPitch": True}] * 4},
                    {"about": {"inning": 1, "halfInning": "bottom"}, "matchup": {"pitcher": {"id": 20}}, "playEvents": [{"isPitch": True}] * 4},
                ]}
        return Response()

    rows, manifest = game_training_rows(123, http_get=fake_get)
    assert sorted((r.bf, r.pitches) for r in rows) == [(3, 12), (3, 13)]
    assert manifest["selection_rule"] == "FIRST_PITCHER_ENCOUNTERED_PER_FIRST_INNING_HALF"
    assert manifest["relief_pitch_events_excluded"] == 6
    assert 99 not in manifest["opener_pitcher_ids"]


def test_refresh_waits_until_official_lineup():
    now = datetime.now(timezone.utc)
    row = {"queue_id": "q1", "event_start_time": (now + timedelta(hours=2)).isoformat(), "player": "Pitcher", "starter_name_at_capture": "Pitcher"}
    def fake(**kwargs):
        return {"starter_name": "Pitcher", "official_lineup_status": "TBD", "can_execute": False}
    result = refresh_queue_row(row, hydrator=fake, now=now)
    assert result["status"] == "WAITING_FOR_OFFICIAL_LINEUP"
    assert result["rerun_required"] is False
    assert result["next_refresh_after_seconds"] == 300
    assert result["can_execute"] is False


def test_refresh_purges_only_changed_starter_row():
    now = datetime.now(timezone.utc)
    row = {"queue_id": "q2", "event_start_time": (now + timedelta(hours=2)).isoformat(), "player": "Old Pitcher", "starter_name_at_capture": "Old Pitcher"}
    def fake(**kwargs):
        return {"starter_name": "New Pitcher", "official_lineup_status": "CONFIRMED", "can_execute": False}
    result = refresh_queue_row(row, hydrator=fake, now=now)
    assert result["status"] == "SLATE_PURGE"
    assert result["terminal_label"] == "SLATE_PURGE"
    assert result["can_execute"] is False


def test_refresh_ready_to_rerun_when_lineup_confirms():
    now = datetime.now(timezone.utc)
    row = {"queue_id": "q3", "event_start_time": (now + timedelta(hours=2)).isoformat(), "player": "Pitcher", "starter_name_at_capture": "Pitcher"}
    def fake(**kwargs):
        return {"starter_name": "Pitcher", "official_lineup_status": "CONFIRMED", "projected_top_four": [{"player": "A"}], "can_execute": False}
    result = refresh_queue_row(row, hydrator=fake, now=now)
    assert result["status"] == "READY_TO_RERUN"
    assert result["rerun_required"] is True
    assert result["probability_publishable"] is False
    assert result["can_execute"] is False


def test_confirmed_refresh_executes_same_empirical_artifact_specialist():
    row = {"line": 15.5, "direction": "MORE", "money_lane_status": "PAYOUT_UNRESOLVED"}
    evidence = {
        "starter_status": "CONFIRMED",
        "official_lineup_status": "CONFIRMED",
        "projected_top_four": [],
        "failure_path_prior": {"status": "RESOLVED_FROM_OFFICIAL_PRIOR_STARTS"},
    }
    artifact = _empirical_artifact()
    result = _rerun(row, evidence, artifact)
    assert result["model_evaluated"] is True
    assert result["model_family"] == "MLB_1IP_CONDITIONAL_TOTAL_PITCH_PMF_V1"
    assert result["model_artifact_version"] == "MLB_1IP_TEST_ARTIFACT_V1"
    assert result["calibration_method"] == "MLB_1IP_EMPIRICAL_TEMPORAL_CAL_V1"
    assert result["calibrated_probability_lower_bound"] <= result["calibrated_probability"]
    assert result["terminal_label"] == "MODEL_QUALIFIED_HOLD"
    assert result["final_refresh_required"] is False
    assert result["probability_publishable"] is False
    assert result["can_execute"] is False
