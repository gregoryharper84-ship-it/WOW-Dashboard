from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pytest

import v17.ncaaf_model_maintenance as maintenance
from ncaaf_candidate_training_runner import NCAAFTrainingRunnerUnavailable
from ncaaf_cfbd_client import CFBDUnavailable
from v17.ncaaf_result_form_candidate import NCAAFResultFormUnavailable


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


def _feature_report(complete_rows: int):
    return {
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
    }


def _install_successful_acquisition(monkeypatch, *, complete_rows: int):
    monkeypatch.setattr(maintenance.CFBDClient, "from_environment", classmethod(lambda cls: object()))
    monkeypatch.setattr(maintenance, "hydrate_cfbd_season", lambda *args, **kwargs: [Snapshot()])
    monkeypatch.setattr(maintenance, "persist_source_snapshots", lambda db, rows: len(rows))
    monkeypatch.setattr(maintenance, "materialize_training_games", lambda db, rows: FakeGames())
    monkeypatch.setattr(
        maintenance,
        "materialize_complete_training_features",
        lambda db: _feature_report(complete_rows),
    )


def test_moneyline_maintenance_acquires_games_without_unused_rating_families(monkeypatch):
    captured = []
    monkeypatch.setattr(maintenance.CFBDClient, "from_environment", classmethod(lambda cls: object()))

    def hydrate(*args, **kwargs):
        captured.append(kwargs)
        return [Snapshot()]

    monkeypatch.setattr(maintenance, "hydrate_cfbd_season", hydrate)
    monkeypatch.setattr(maintenance, "persist_source_snapshots", lambda db, rows: len(rows))
    monkeypatch.setattr(maintenance, "materialize_training_games", lambda db, rows: FakeGames())
    monkeypatch.setattr(maintenance, "materialize_complete_training_features", lambda db: _feature_report(0))
    monkeypatch.setattr(
        maintenance,
        "train_result_form_candidate",
        lambda db, *, training_code_sha: {
            "ok": True,
            "lifecycle_state": "CANDIDATE",
            "probability_publishable": False,
            "can_execute": False,
        },
    )

    result = maintenance.run_ncaaf_model_maintenance(
        DummyDB(), seasons=[2026], weeks=[1], training_code_sha="a" * 40
    )

    assert len(captured) == 1
    assert captured[0]["rating_families"] == ()
    assert captured[0]["classification"] == "fbs"
    assert result["fresh_acquisition_complete"] is True
    assert result["maintenance_degraded"] is False
    assert result["can_execute"] is False


def test_missing_cfbd_configuration_uses_governed_persisted_corpus(monkeypatch):
    def unavailable(_cls):
        raise CFBDUnavailable("CFBD_API_KEY_MISSING", "missing")

    fallback_calls = []

    def fallback(_db, *, training_code_sha):
        fallback_calls.append(training_code_sha)
        return {
            "ok": True,
            "code": "NCAAF_RESULT_FORM_CANDIDATE_PERSISTED",
            "model_artifact_version": "result-form-v1",
            "feature_schema_version": "NCAAF_RESULT_FORM_PRIOR_V1",
            "eligible_rows": 1200,
            "metrics": {"research_screen_pass": True},
            "research_screen_pass": True,
            "lifecycle_state": "CANDIDATE",
            "automatic_certification": False,
            "automatic_promotion": False,
            "probability_publishable": False,
            "can_execute": False,
        }

    monkeypatch.setattr(maintenance.CFBDClient, "from_environment", classmethod(unavailable))
    monkeypatch.setattr(maintenance, "materialize_complete_training_features", lambda db: _feature_report(0))
    monkeypatch.setattr(maintenance, "train_result_form_candidate", fallback)
    result = maintenance.run_ncaaf_model_maintenance(
        DummyDB(), seasons=[2026], weeks=[1], training_code_sha="a" * 40
    )

    assert result["status"] == "CANDIDATE_EVIDENCE_UPDATED"
    assert result["fresh_acquisition_complete"] is False
    assert result["maintenance_degraded"] is True
    assert "CFBD_API_KEY_MISSING" in result["blockers"]
    assert result["acquisition"][0]["status"] == "BLOCKED_USING_PERSISTED_CORPUS"
    assert result["candidate_lane"] == "RESULT_FORM_PRIOR_V1"
    assert fallback_calls == ["a" * 40]
    assert result["automatic_certification"] is False
    assert result["automatic_promotion"] is False
    assert result["probability_publishable"] is False
    assert result["can_execute"] is False


def test_incomplete_rich_feature_corpus_uses_separate_prior_result_candidate(monkeypatch):
    _install_successful_acquisition(monkeypatch, complete_rows=299)
    rich_called = []
    fallback_called = []
    monkeypatch.setattr(maintenance, "train_and_persist_candidate", lambda *a, **k: rich_called.append(True))

    def fallback(_db, *, training_code_sha):
        fallback_called.append(training_code_sha)
        return {
            "ok": True,
            "code": "NCAAF_RESULT_FORM_CANDIDATE_PERSISTED",
            "model_artifact_version": "result-form-v1",
            "feature_schema_version": "NCAAF_RESULT_FORM_PRIOR_V1",
            "eligible_rows": 1200,
            "metrics": {"research_screen_pass": True},
            "research_screen_pass": True,
            "lifecycle_state": "CANDIDATE",
            "automatic_certification": False,
            "automatic_promotion": False,
            "probability_publishable": False,
            "can_execute": False,
        }

    monkeypatch.setattr(maintenance, "train_result_form_candidate", fallback)
    result = maintenance.run_ncaaf_model_maintenance(
        DummyDB(), seasons=[2026], weeks=[1], training_code_sha="a" * 40
    )
    assert rich_called == []
    assert fallback_called == ["a" * 40]
    assert result["status"] == "CANDIDATE_EVIDENCE_UPDATED"
    assert result["candidate_lane"] == "RESULT_FORM_PRIOR_V1"
    assert result["candidate_training"]["lifecycle_state"] == "CANDIDATE"
    assert result["feature_compilation"]["complete_feature_rows"] == 299
    assert result["feature_compilation"]["market_features_used"] is False
    assert result["automatic_certification"] is False
    assert result["automatic_promotion"] is False
    assert result["probability_publishable"] is False
    assert result["can_execute"] is False


def test_prior_result_fallback_failure_preserves_typed_blocker(monkeypatch):
    _install_successful_acquisition(monkeypatch, complete_rows=0)

    def fail(*_args, **_kwargs):
        raise NCAAFResultFormUnavailable("NCAAF_RESULT_FORM_SAMPLE_INSUFFICIENT", "not enough prior-result rows")

    monkeypatch.setattr(maintenance, "train_result_form_candidate", fail)
    result = maintenance.run_ncaaf_model_maintenance(
        DummyDB(), seasons=[2026], weeks=[1], training_code_sha="d" * 40
    )
    assert result["status"] == "BLOCKED"
    assert result["candidate_lane"] == "RESULT_FORM_PRIOR_V1"
    assert result["training_blocker"]["code"] == "NCAAF_RESULT_FORM_SAMPLE_INSUFFICIENT"
    assert "NCAAF_RESULT_FORM_SAMPLE_INSUFFICIENT" in result["blockers"]
    assert result["probability_publishable"] is False
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
    assert result["candidate_lane"] == "RICH_FEATURES_V1"
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


def test_ncaaf_live_maintenance_never_runs_on_push():
    repo_root = Path(__file__).resolve().parents[2]
    workflow = (repo_root / ".github" / "workflows" / "wow-v17-ncaaf-model-maintenance.yml").read_text(
        encoding="utf-8"
    )
    assert "if: github.event_name == 'schedule' || github.event_name == 'workflow_dispatch'" in workflow
    assert "(github.event_name == 'push' && github.ref == 'refs/heads/main')" not in workflow
    assert 'WOW_CAN_EXECUTE: "false"' in workflow
    assert 'WOW_DRY_RUN_ONLY: "true"' in workflow
