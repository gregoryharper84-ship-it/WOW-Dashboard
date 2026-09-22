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
        "league": sport,
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
    assert result["lane_id"] == "NBA:NBA:NBA_MODEL_V1"
    assert result["probability_publishable"] is False
    assert result["can_execute"] is False


def test_nhl_candidate_research_failure_cannot_advance():
    result = assess_candidate("NHL", _candidate("NHL", research_screen_pass=False)).as_dict()
    assert result["status"] == "PRODUCTION_MODEL_PRESENT"
    # NHL production capability remains separate from challenger quality.
    assert result["can_execute"] is False


def test_candidate_research_failure_is_typed_for_nonproduction_lane():
    result = assess_candidate("SOCCER", _candidate("SOCCER", league="LIGUE_1", research_screen_pass=False)).as_dict()
    assert result["status"] == "RESEARCH_SCREEN_FAILED"
    assert "RESEARCH_SCREEN_FAILED" in result["blockers"]
    assert result["probability_publishable"] is False
    assert result["can_execute"] is False


def test_candidate_research_failure_is_typed_for_nonproduction_lane():
    result = assess_candidate("SOCCER", _candidate("SOCCER", league="LIGUE_1", research_screen_pass=False)).as_dict()
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
    assert rows["NBA"]["lane_count"] == 1
    assert rows["BOXING"]["status"] == "BUILD_REQUIRED"
    assert report["can_execute"] is False


def test_soccer_competition_lanes_do_not_mask_each_other():
    epl = _candidate(
        "SOCCER",
        league="EPL",
        model_family="SOCCER_EPL_DYNAMIC_TEAM_STATE_1X2_V2",
        model_artifact_version="soccer-epl-v2",
        source_review_status="REQUIRED",
        research_screen_pass=True,
    )
    ligue1 = _candidate(
        "SOCCER",
        candidate_id="22222222-2222-2222-2222-222222222222",
        created_at="2026-09-19T17:00:00+00:00",
        league="LIGUE_1",
        model_family="SOCCER_LIGUE_1_DYNAMIC_TEAM_STATE_1X2_V2",
        model_artifact_version="soccer-ligue1-v2",
        research_screen_pass=False,
    )
    report = build_certification_report([epl, ligue1])
    soccer = next(row for row in report["sports"] if row["sport"] == "SOCCER")
    assert soccer["lane_count"] == 2
    assert soccer["research_pass_lane_count"] == 1
    assert soccer["status"] == "SOURCE_REVIEW_PENDING"
    lanes = {row["league"]: row for row in soccer["lanes"]}
    assert lanes["EPL"]["status"] == "SOURCE_REVIEW_PENDING"
    assert lanes["LIGUE_1"]["status"] == "RESEARCH_SCREEN_FAILED"


def test_replay_evidence_is_lane_specific():
    atp = _candidate(
        "TENNIS",
        league="ATP",
        model_family="TENNIS_MAIN_TOUR_MATCH_WIN_LOGIT_V1_ATP",
        source_review_status="PASS",
    )
    wta = _candidate(
        "TENNIS",
        candidate_id="33333333-3333-3333-3333-333333333333",
        league="WTA",
        model_family="TENNIS_MAIN_TOUR_MATCH_WIN_LOGIT_V1_WTA",
        source_review_status="PASS",
    )
    report = build_certification_report(
        [atp, wta],
        replay_evidence_by_lane={"TENNIS:ATP:TENNIS_MAIN_TOUR_MATCH_WIN_LOGIT_V1_ATP": True},
    )
    tennis = next(row for row in report["sports"] if row["sport"] == "TENNIS")
    lanes = {row["league"]: row for row in tennis["lanes"]}
    assert lanes["ATP"]["status"] == "CERTIFICATION_REPLAY_PASS"
    assert lanes["WTA"]["status"] == "CERTIFICATION_REPLAY_BLOCKED"
    assert tennis["status"] == "CERTIFICATION_REPLAY_PASS"
    assert tennis["probability_publishable"] is False


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


def test_database_report_reads_latest_candidate_per_lane_not_per_sport():
    older_nba = _candidate(created_at="2026-09-18T16:00:00+00:00", research_screen_pass=False)
    newer_nba = _candidate(created_at="2026-09-19T16:00:00+00:00", research_screen_pass=True)
    epl = _candidate(
        "SOCCER",
        league="EPL",
        model_family="SOCCER_EPL_MODEL",
        model_artifact_version="epl-v1",
    )
    serie_a = _candidate(
        "SOCCER",
        candidate_id="44444444-4444-4444-4444-444444444444",
        league="SERIE_A",
        model_family="SOCCER_SERIE_A_MODEL",
        model_artifact_version="serie-a-v1",
    )
    report = run_certification_replay(_DB([older_nba, newer_nba, epl, serie_a]))
    nba = next(row for row in report["sports"] if row["sport"] == "NBA")
    soccer = next(row for row in report["sports"] if row["sport"] == "SOCCER")
    assert nba["research_pass_lane_count"] == 1
    assert soccer["lane_count"] == 2


def test_route_install_is_idempotent():
    app = FastAPI()
    install_team_event_certification_replay_route(app, auth_dependency=lambda: None, db_client_fn=lambda: _DB())
    install_team_event_certification_replay_route(app, auth_dependency=lambda: None, db_client_fn=lambda: _DB())
    paths = [route.path for route in app.router.routes]
    assert paths.count("/internal/v17/team-event-certification-replay") == 1
