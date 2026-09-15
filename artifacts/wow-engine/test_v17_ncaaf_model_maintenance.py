from __future__ import annotations

from dataclasses import dataclass

import pytest

import v17.ncaaf_model_maintenance as maintenance
from ncaaf_candidate_training_runner import NCAAFTrainingRunnerUnavailable
from ncaaf_cfbd_client import CFBDUnavailable


class DummyDB:
    pass


@dataclass
class FakeGames:
    candidate_rows: int = 4
    persisted_rows: int = 4
    skipped_rows: int = 0
    blocker_codes: tuple[str, ...] = ()
    can_execute: bool = False


class Snapshot:
    def __init__(self, *, blocker_codes=()):
        self.blocker_codes = list(blocker_codes)


def _install_successful_acquisition(monkeypatch, *, complete_rows: int):
    monkeypatch.setattr(maintenance.CFBDClient, "from_environment", classmethod(lambda cls: object()))
    monkeypatch.setattr(
        maintenance,
        "hydrate_cfbd_season",
        lambda *args, **kwargs: [Snapshot()],
    )
    monkeypatch.setattr(maintenance, "persist_source_snapshots", lambda db, rows: len(rows))
    monkeypatch.setattr(maintenance, "materialize_training_games", lambda db, rows: FakeGames())
    monkeypatch.setattr(
        maintenance,
        "materialize_complete_training_features",
        lambda db: {
            "status": "COMPLETE" if complete_rows else "BLOCKED",
            "games_seen": complete_rows,
            "evidence_rows_seen": complete_rows * 29,
            "features_persisted": complete_rows,
            "features_existing": 0,
            "complete_feature_rows": complete_rows,
            "blocked_games": 0,
            "blocker_counts": {},
            "blocker_samples": [],
            "feature_schema_version": "NCAAF_FEATURES_V1",
            "compiler_version": "NCAAF_EVIDENCE_FEATURE_COMPILER_V1",
            "market_features_used": False,
            "probability_publishable": False,
            "can_execute": False,
        },
    )


def test_missing_cfbd_configuration_fails_closed_before_any_model_work(monkeypatch):
    def unavailable(_cls):
        raise CFBDUnavailable("CFBD_API_KEY_MISSING", "missing")

    monkeypatch.setattr(maintenance.CFBDClient, "from_environment", classmethod(unavailable))
    result = maintenance.run_ncaaf_model_maintenance(DummyDB(), seasons=[2026], weeks=[1])
    assert result["status"] == "BLOCKED"
    assert result["code"] == "CFBD_API_KEY_MISSING"
    assert result["blocked_stage"] == "CFBD_ACQUISITION"
    assert result["automatic_certification"] is False
    assert result["automatic_promotion"] is False
    assert result["probability_publishable"] is False
    assert result["can_execute"] is False


def test_incomplete_feature_corpus_does_not_attempt_candidate_training(monkeypatch):
    _install_successful_acquisition(monkeypatch, complete_rows=299)
    called = []
    monkeypatch.setattr(maintenance, "train_and_persist_candidate", lambda *a, **k: called.append(True))
    result = maintenance.run_ncaaf_model_maintenance(
        DummyDB(), seasons=[2026], weeks=[1], training_code_sha="a" * 40
    )
    assert called == []
    assert result["status"] == "BLOCKED"
    assert result["training_blocker"]["code"] == "NCAAF_COMPLETE_TRAINING_ROWS_INSUFFICIENT"
    assert result["feature_compilation"]["complete_feature_rows"] == 299
    assert result["feature_compilation"]["market_features_used"] is False
    assert result["can_execute"] is False


def test_complete_corpus_can_only_create_candidate_evidence(monkeypatch):
    _install_successful_acquisition(monkeypatch, complete_rows=300)

    def train(_db, *, training_code_sha):
        assert training_code_sha == "b" * 40
        return {
            "ok": True,
            "code": "NCAAF_CANDIDATE_ARTIFACTS_PERSISTED",
            "model_artifact_version": "candidate-v1",
            "calibrator_version": "candidate-cal-v1",
            "training_rows": 300,
            "metrics": {"research_screen_pass": True},
            "lifecycle_state": "CANDIDATE",
            "calibration_health_status": "BLOCKED",
            "probability_publishable": False,
            "can_execute": False,
        }

    monkeypatch.setattr(maintenance, "train_and_persist_candidate", train)
    result = maintenance.run_ncaaf_model_maintenance(
        DummyDB(), seasons=[2026], weeks=[1], training_code_sha="b" * 40
    )
    assert result["status"] == "CANDIDATE_EVIDENCE_UPDATED"
    assert result["candidate_training"]["lifecycle_state"] == "CANDIDATE"
    assert result["candidate_training"]["calibration_health_status"] == "BLOCKED"
    assert result["automatic_certification"] is False
    assert result["automatic_promotion"] is False
    assert result["probability_publishable"] is False
    assert result["can_execute"] is False


def test_candidate_trainer_failure_is_preserved_as_typed_blocker(monkeypatch):
    _install_successful_acquisition(monkeypatch, complete_rows=300)

    def fail(*_args, **_kwargs):
        raise NCAAFTrainingRunnerUnavailable("NCAAF_MODEL_TEST_GATE_FAILED", "gate")

    monkeypatch.setattr(maintenance, "train_and_persist_candidate", fail)
    result = maintenance.run_ncaaf_model_maintenance(
        DummyDB(), seasons=[2026], weeks=[1], training_code_sha="c" * 40
    )
    assert result["status"] == "BLOCKED"
    assert result["training_blocker"]["code"] == "NCAAF_MODEL_TEST_GATE_FAILED"
    assert "NCAAF_MODEL_TEST_GATE_FAILED" in result["blockers"]
    assert result["automatic_certification"] is False
    assert result["can_execute"] is False


def test_training_code_identity_is_required_for_auditable_candidate(monkeypatch):
    _install_successful_acquisition(monkeypatch, complete_rows=300)
    monkeypatch.delenv("RENDER_GIT_COMMIT", raising=False)
    result = maintenance.run_ncaaf_model_maintenance(DummyDB(), seasons=[2026], weeks=[1])
    assert result["status"] == "BLOCKED"
    assert result["training_blocker"]["code"] == "NCAAF_TRAINING_CODE_SHA_UNAVAILABLE"
    assert result["candidate_training"] is None
    assert result["can_execute"] is False
