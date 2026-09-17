from __future__ import annotations

import pytest
from fastapi import HTTPException

from v17.first_six_open_data_maintenance import _persist_team_state_batch
import v17.team_state_runner_rescue as rescue


class _FakeExecute:
    def execute(self):
        return type("Result", (), {"data": []})()


class _FakeTable:
    def __init__(self, db, name):
        self.db = db
        self.name = name

    def upsert(self, rows, *, on_conflict, ignore_duplicates):
        self.db.calls.append(
            {
                "table": self.name,
                "rows": rows,
                "on_conflict": on_conflict,
                "ignore_duplicates": ignore_duplicates,
            }
        )
        return _FakeExecute()


class _FakeDB:
    def __init__(self):
        self.calls = []

    def table(self, name):
        return _FakeTable(self, name)


def _training_row():
    return {
        "sport": "MLB",
        "league": "MLB",
        "official_event_id": "MLB:test",
        "event_start_time": "2026-01-01T00:00:00+00:00",
        "feature_as_of": "2025-12-31T23:59:59+00:00",
        "feature_schema_version": "MLB_DYNAMIC_TEAM_STATE_FEATURES_V2",
        "model_family": "MLB_DYNAMIC_TEAM_STATE_LOGIT_V2",
        "features": {"x": 1.0},
        "outcome_json": {"home_win": True},
        "source_manifest": {"program": "LLP_DYNAMIC_TEAM_STATE_CHALLENGER_V1"},
        "source_manifest_sha256": "a" * 64,
        "historical_reconstruction": True,
        "archived_pregame_snapshot": False,
        "market_features_used": False,
        "can_execute": False,
    }


def _artifact():
    return {
        "sport": "MLB",
        "league": "MLB",
        "market_family": "OUTRIGHT_WINNER",
        "model_family": "MLB_DYNAMIC_TEAM_STATE_LOGIT_V2",
        "model_artifact_version": "MLB_DYNAMIC_TEAM_STATE_LOGIT_V2_test",
        "feature_schema_version": "MLB_DYNAMIC_TEAM_STATE_FEATURES_V2",
        "source_policy_id": "TEAM_STATE_DYNAMIC_PRIOR_ONLY_V1",
        "training_dataset_hash": "b" * 64,
        "training_code_sha": "c" * 40,
        "artifact_checksum": "d" * 64,
        "artifact_payload": {},
        "calibrator_payload": {},
        "validation_metrics": {},
        "training_rows": 500,
        "calibration_rows": 100,
        "test_rows": 100,
        "research_screen_pass": True,
        "source_review_status": "REQUIRED",
        "lifecycle_state": "CANDIDATE",
        "promoted": False,
        "active": False,
        "automatic_certification": False,
        "automatic_promotion": False,
        "probability_publishable": False,
        "can_execute": False,
    }


def test_persist_endpoint_accepts_only_governed_team_state_batches():
    db = _FakeDB()
    result = _persist_team_state_batch(
        db,
        {
            "table": "wow_d1_training_rows",
            "rows": [_training_row()],
            "on_conflict": "sport,official_event_id,feature_schema_version,source_manifest_sha256",
            "ignore_duplicates": True,
        },
    )
    assert result["status"] == "PERSISTED"
    assert result["can_execute"] is False
    assert len(db.calls) == 1

    artifact_result = _persist_team_state_batch(
        db,
        {
            "table": "wow_d1_candidate_artifacts",
            "rows": [_artifact()],
            "on_conflict": "model_artifact_version",
            "ignore_duplicates": True,
        },
    )
    assert artifact_result["status"] == "PERSISTED"
    assert len(db.calls) == 2


@pytest.mark.parametrize(
    "mutator",
    [
        lambda payload: payload.update({"table": "wow_predictions"}),
        lambda payload: payload["rows"][0].update({"can_execute": True}),
        lambda payload: payload["rows"][0].update({"sport": "NFL"}),
        lambda payload: payload["rows"][0].update({"model_family": "MLB_OTHER_MODEL"}),
        lambda payload: payload["rows"][0].update({"market_features_used": True}),
    ],
)
def test_persist_endpoint_rejects_scope_or_governance_escape(mutator):
    payload = {
        "table": "wow_d1_training_rows",
        "rows": [_training_row()],
        "on_conflict": "sport,official_event_id,feature_schema_version,source_manifest_sha256",
        "ignore_duplicates": True,
    }
    mutator(payload)
    with pytest.raises(HTTPException):
        _persist_team_state_batch(_FakeDB(), payload)


def test_runner_captures_existing_upserts_without_db_credentials(monkeypatch):
    def fake_run(client, *, scope, training_code_sha):
        client.table("wow_d1_training_rows").upsert(
            [_training_row()],
            on_conflict="sport,official_event_id,feature_schema_version,source_manifest_sha256",
            ignore_duplicates=True,
        ).execute()
        client.table("wow_d1_candidate_artifacts").upsert(
            [_artifact()],
            on_conflict="model_artifact_version",
            ignore_duplicates=True,
        ).execute()
        return {
            "status": "COMPLETED_WITH_EVIDENCE",
            "program": "LLP_DYNAMIC_TEAM_STATE_CHALLENGER_V1",
            "scope": scope,
            "candidate_rows_updated": 1,
            "candidate_rows_blocked": 0,
            "rows": [{"sport": scope, "status": "CANDIDATE_EVIDENCE_UPDATED", "can_execute": False}],
            "automatic_certification": False,
            "automatic_promotion": False,
            "probability_publishable": False,
            "can_execute": False,
        }

    monkeypatch.setattr(rescue, "run_team_state_scope", fake_run)
    package = rescue.prepare_source_heavy_scope(scope="MLB", training_code_sha="1" * 40)
    assert package["batch_count"] == 2
    assert {batch["table"] for batch in package["batches"]} == {
        "wow_d1_training_rows",
        "wow_d1_candidate_artifacts",
    }
    assert package["can_execute"] is False


def test_runner_does_not_persist_blocked_scope(monkeypatch):
    def fake_run(client, *, scope, training_code_sha):
        return {
            "status": "COMPLETED_NO_CANDIDATE_SURVIVORS",
            "program": "LLP_DYNAMIC_TEAM_STATE_CHALLENGER_V1",
            "scope": scope,
            "candidate_rows_updated": 0,
            "candidate_rows_blocked": 1,
            "rows": [{"sport": scope, "status": "BLOCKED", "code": "SOURCE_UNAVAILABLE", "can_execute": False}],
            "automatic_certification": False,
            "automatic_promotion": False,
            "probability_publishable": False,
            "can_execute": False,
        }

    monkeypatch.setattr(rescue, "run_team_state_scope", fake_run)
    package = rescue.prepare_source_heavy_scope(scope="NCAAB", training_code_sha="2" * 40)
    assert package["batch_count"] == 0
    assert package["result"]["candidate_rows_updated"] == 0
