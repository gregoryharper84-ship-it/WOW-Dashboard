from types import SimpleNamespace

from fastapi import FastAPI

from v17.team_event_certification_replay import (
    assess_candidate,
    build_certification_report,
    install_team_event_certification_replay_route,
    run_certification_replay,
)


def _candidate(sport="NBA", **overrides):
    row = {
        "candidate_id": "11111111-1111-1111-1111-111111111111",
        "created_at": "2026-09-19T16:00:00+00:00",
        "sport": sport,
        "model_family": f"{sport}_MODEL_V1",
        "model_artifact_version": f"{sport}_MODEL_V1_deadbeef",
        "training_dataset_hash": "a" * 64,
        "training_code_sha": "b" * 40,
        "artifact_checksum": "c" * 64,
        "training_rows": 600,
        "calibration_rows": 200,
        "test_rows": 200,
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
    row.update(overrides)
    return row


def test_source_review_and_replay_are_required_after_research_pass():
    result = assess_candidate("NBA", _candidate()).as_dict()
    assert result["status"] == "SOURCE_REVIEW_PENDING"
    assert "SOURCE_REVIEW_REQUIRED" in result["blockers"]
    assert "PROSPECTIVE_OR_CERTIFICATION_REPLAY_EVIDENCE_REQUIRED" in result["blockers"]
    assert result["probability_publishable"] is False
    assert result["can_execute"] is False


def test_research_failure_cannot_advance():
    result = assess_candidate("NHL", _candidate("NHL", research_screen_pass=False)).as_dict()
    assert result["status"] == "RESEARCH_SCREEN_FAILED"
    assert "RESEARCH_SCREEN_FAILED" in result["blockers"]


def test_build_required_sport_cannot_be_manufactured_from_missing_candidate():
    result = assess_candidate("PGA", None).as_dict()
    assert result["status"] == "BUILD_REQUIRED"
    assert result["blockers"] == ["FITTED_CANDIDATE_PIPELINE_REQUIRED"]


def test_replay_pass_is_still_non_promoting_and_non_publishable():
    result = assess_candidate(
        "NBA",
        _candidate(source_review_status="PASS"),
        replay_evidence_pass=True,
    ).as_dict()
    assert result["status"] == "CERTIFICATION_REPLAY_PASS"
    assert result["blockers"] == []
    assert result["automatic_certification"] is False
    assert result["automatic_promotion"] is False
    assert result["probability_publishable"] is False
    assert result["can_execute"] is False


def test_production_model_lanes_remain_separate_from_candidate_replay():
    report = build_certification_report([_candidate("NBA")])
    rows = {row["sport"]: row for row in report["sports"]}
    assert rows["MLB"]["status"] == "PRODUCTION_MODEL_PRESENT"
    assert rows["NFL"]["status"] == "PRODUCTION_MODEL_PRESENT"
    assert rows["NBA"]["status"] == "SOURCE_REVIEW_PENDING"
    assert rows["BOXING"]["status"] == "BUILD_REQUIRED"
    assert report["can_execute"] is False


class _Query:
    def __init__(self, rows):
        self.rows = rows
    def select(self, *_args, **_kwargs):
        return self
    def order(self, *_args, **_kwargs):
        return self
    def limit(self, *_args, **_kwargs):
        return self
    def execute(self):
        return SimpleNamespace(data=self.rows)


class _DB:
    def __init__(self, rows=None):
        self.rows = list(rows or [])
    def table(self, _name):
        return _Query(self.rows)


def test_database_report_reads_latest_candidate_rows_only():
    older = _candidate(created_at="2026-09-18T16:00:00+00:00", research_screen_pass=False)
    newer = _candidate(created_at="2026-09-19T16:00:00+00:00", research_screen_pass=True)
    report = run_certification_replay(_DB([older, newer]))
    nba = next(row for row in report["sports"] if row["sport"] == "NBA")
    assert nba["research_screen_pass"] is True


def test_route_install_is_idempotent():
    app = FastAPI()
    install_team_event_certification_replay_route(app, auth_dependency=lambda: None, db_client_fn=lambda: _DB())
    install_team_event_certification_replay_route(app, auth_dependency=lambda: None, db_client_fn=lambda: _DB())
    paths = [route.path for route in app.router.routes]
    assert paths.count("/internal/v17/team-event-certification-replay") == 1
