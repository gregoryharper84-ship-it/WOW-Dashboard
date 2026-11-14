from __future__ import annotations

from datetime import date, datetime, timezone
import os
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from v17.nfl_forward_cohort_preflight import (
    assess_slate_event,
    current_slate_date,
    project_forward_cohort,
    slate_window,
)

SLATE = "2026-11-15"
# 1:00pm ET Sunday = 18:00Z; 8:20pm ET Sunday Night Football = 01:20Z Monday.
KICKOFF = "2026-11-15T18:00:00+00:00"
SNF_KICKOFF = "2026-11-16T01:20:00+00:00"


class _ReadOnlyQuery:
    """PostgREST-shaped reader that hard-fails on any write attempt."""

    def __init__(self, db, name):
        self.db = db
        self.name = name
        self.key = None
        self.start = self.end = None
        self.columns = None

    def select(self, columns):
        self.columns = columns
        return self

    def order(self, key, desc=False):
        self.key = key
        self.db.orders.append((self.name, key))
        return self

    def range(self, start, end):
        self.start, self.end = start, end
        return self

    def execute(self):
        if not self.key:
            raise AssertionError("forward preflight reads require explicit ordering")
        ordered = sorted(self.db.rows.get(self.name, []), key=lambda row: str(row.get(self.key) or ""))
        return SimpleNamespace(data=ordered[self.start:self.end + 1])

    def insert(self, *_args, **_kwargs):
        raise AssertionError("forward cohort preflight must never write")

    def upsert(self, *_args, **_kwargs):
        raise AssertionError("forward cohort preflight must never write")

    def update(self, *_args, **_kwargs):
        raise AssertionError("forward cohort preflight must never write")

    def delete(self, *_args, **_kwargs):
        raise AssertionError("forward cohort preflight must never write")


class _ReadOnlyDB:
    def __init__(self, *, graded=95, predictions=()):
        # Historical grades alone are not the grader's current cohort. Supply
        # immutable pregame predictions and current non-tie outcomes as well.
        historic_ids = [f"graded-{i:04d}" for i in range(graded)]
        history = [
            _prediction(event_id, kickoff="2026-11-01T18:00:00+00:00",
                        created="2026-11-01T16:00:00+00:00")
            for event_id in historic_ids
        ]
        self.rows = {
            "wow_nfl_forward_shadow_grades": [
                {"grade_id": f"grade-{i:04d}", "official_event_id": event_id}
                for i, event_id in enumerate(historic_ids)
            ],
            "wow_nfl_event_predictions": history + list(predictions),
            "wow_nfl_training_games": [
                {"game_id": event_id, "home_score": 20, "away_score": 13,
                 "home_win": True, "tie": False}
                for event_id in historic_ids
            ],
        }
        self.orders = []

    def table(self, name):
        return _ReadOnlyQuery(self, name)


def _prediction(
    event_id,
    *,
    prediction_id=None,
    kickoff=KICKOFF,
    created="2026-11-15T16:00:00+00:00",
    p=0.62,
    lower=0.55,
    upper=0.70,
    package_valid=True,
    provenance_complete=True,
    publishable=True,
    can_execute=False,
):
    return {
        "event_prediction_id": prediction_id or f"pred-{event_id}",
        "official_event_id": event_id,
        "event_start_time_utc": kickoff,
        "created_at": created,
        "model_version": "NFL_EVENT_LOGREG_PLATT_V1_test",
        "home_team": "BBB",
        "away_team": "AAA",
        "selected_participant": "BBB",
        "calibrated_selection_probability": p,
        "calibrated_selection_lower_bound": lower,
        "calibrated_selection_upper_bound": upper,
        "model_package_valid": package_valid,
        "provenance_complete": provenance_complete,
        "model_probability_publishable": publishable,
        "can_execute": can_execute,
    }


def test_slate_window_keeps_sunday_night_game_past_utc_midnight():
    start, end = slate_window(SLATE)
    assert start == datetime(2026, 11, 15, 5, 0, tzinfo=timezone.utc)
    assert end == datetime(2026, 11, 16, 5, 0, tzinfo=timezone.utc)
    assert start <= datetime.fromisoformat(SNF_KICKOFF) < end
    # A Monday midnight-ET kickoff belongs to the next gameday, not this slate.
    assert end <= datetime(2026, 11, 16, 6, 0, tzinfo=timezone.utc)


def test_current_slate_date_uses_us_eastern_gameday():
    # 02:00Z Monday is 10:00pm Sunday ET: still Sunday's slate.
    assert current_slate_date(
        datetime(2026, 11, 16, 2, 0, tzinfo=timezone.utc)
    ).isoformat() == "2026-11-15"
    # 12:30Z Monday is 07:30am ET Monday: the next slate.
    assert current_slate_date(
        datetime(2026, 11, 16, 12, 30, tzinfo=timezone.utc)
    ).isoformat() == "2026-11-16"


def test_twelve_game_slate_closes_the_gap_but_still_never_certifies():
    predictions = [_prediction(f"slate-{i:02d}") for i in range(12)]
    predictions[-1]["event_start_time_utc"] = SNF_KICKOFF
    predictions[-1]["created_at"] = "2026-11-16T00:30:00+00:00"
    db = _ReadOnlyDB(predictions=predictions)

    report = project_forward_cohort(
        db, slate_date=SLATE, expected_slate_games=12, min_forward=100
    )

    assert report["graded_n"] == 95
    assert report["current_gap_to_minimum"] == 5
    assert report["observed_slate_games"] == 12
    assert report["slate_coverage_verified"] is True
    assert report["eligible_pregame_n"] == 12
    assert report["cannot_grade_n"] == 0
    assert report["projected_graded_n"] == 107
    assert report["projected_gap_to_minimum"] == 0
    assert report["status"] == "PROJECTED_TO_REACH_MINIMUM"
    assert report["blockers"] == []
    assert report["projection_is_certification_evidence"] is False
    assert report["certification_recommendation"] == "DO_NOT_CERTIFY_YET"
    assert report["automatic_certification"] is False
    assert report["automatic_promotion"] is False
    assert report["can_execute"] is False
    assert {event["status"] for event in report["slate_events"]} == {"ELIGIBLE_PREGAME"}
    assert report["pregame_eligibility_is_not_a_grade"] is True
    assert report["settlement_requirements"] == [
        "SETTLED_NON_TIE_OUTCOME",
        "HOME_WIN_PRESENT",
    ]


def test_short_slate_is_projected_short_and_never_lowers_the_gate():
    db = _ReadOnlyDB(predictions=[_prediction(f"slate-{i:02d}") for i in range(3)])

    report = project_forward_cohort(db, slate_date=SLATE, min_forward=100)

    assert report["projected_graded_n"] == 98
    assert report["projected_gap_to_minimum"] == 2
    assert report["status"] == "PROJECTED_SHORT_OF_MINIMUM"
    assert "FORWARD_GRADED_PROJECTED_98_LT_100" in report["blockers"]
    assert report["minimum_forward_required"] == 100
    assert report["certification_recommendation"] == "DO_NOT_CERTIFY_YET"


def test_each_unrecoverable_prediction_defect_is_named_before_kickoff():
    predictions = [
        _prediction("late", created="2026-11-15T19:00:00+00:00"),
        _prediction("unpublished", publishable=False),
        _prediction("bad-bounds", p=0.70, lower=0.75, upper=0.80),
        _prediction("unsafe", can_execute=True),
        _prediction("valid"),
    ]
    db = _ReadOnlyDB(predictions=predictions)

    report = project_forward_cohort(db, slate_date=SLATE, expected_slate_games=5)

    reasons = {
        event["official_event_id"]: event["cannot_grade_reason"]
        for event in report["slate_events"]
    }
    assert reasons == {
        "late": "PREDICTION_ONLY_AFTER_KICKOFF",
        "unpublished": "PUBLICATION_FLAGS_INCOMPLETE",
        "bad-bounds": "CALIBRATED_PROBABILITY_BOUNDS_INVALID",
        "unsafe": "CAN_EXECUTE_NOT_FALSE",
        "valid": None,
    }
    assert report["eligible_pregame_n"] == 1
    assert report["cannot_grade_n"] == 4
    assert "NFL_FORWARD_SLATE_EVENTS_CANNOT_GRADE:4" in report["blockers"]
    # Invalid bounds and `can_execute=True` both make the grader raise for the
    # whole run, not just this event, so the projection holds instead of
    # reporting the slate as ready.
    assert report["ledger_violation_n"] == 2
    assert {violation["code"] for violation in report["ledger_violations"]} == {
        "CALIBRATED_PROBABILITY_BOUNDS_INVALID",
        "CAN_EXECUTE_MUST_BE_FALSE",
    }
    assert "PREDICTION_LEDGER_WOULD_FAIL_GRADER:2" in report["blockers"]
    assert report["status"] == "HOLD_PREDICTION_LEDGER_INVALID"
    assert report["can_execute"] is False


def test_latest_valid_pregame_row_wins_and_graded_events_are_not_double_counted():
    rows = [
        _prediction("repeat", prediction_id="a", p=0.60),
        _prediction("repeat", prediction_id="b", p=0.64, created="2026-11-15T17:00:00+00:00"),
        # Written after kickoff and therefore dropped, but internally consistent:
        # the grader validates bounds before it drops late rows.
        _prediction(
            "repeat",
            prediction_id="c",
            p=0.99,
            lower=0.95,
            upper=0.999,
            created="2026-11-15T18:30:00+00:00",
        ),
        _prediction("already"),
    ]
    # 99 prior grades plus the slate's own already-graded game: the cohort is
    # counted by distinct event, so the slate event adds exactly one.
    db = _ReadOnlyDB(graded=99, predictions=rows)
    db.rows["wow_nfl_forward_shadow_grades"].append(
        {"grade_id": "grade-already", "official_event_id": "already"}
    )
    db.rows["wow_nfl_training_games"].append(
        {"game_id": "already", "home_score": 20, "away_score": 13,
         "home_win": True, "tie": False}
    )

    report = project_forward_cohort(db, slate_date=SLATE, min_forward=100)

    statuses = {event["official_event_id"]: event for event in report["slate_events"]}
    assert statuses["repeat"]["status"] == "ELIGIBLE_PREGAME"
    assert statuses["repeat"]["event_prediction_id"] == "b"
    assert statuses["already"]["status"] == "ALREADY_GRADED"
    assert report["graded_n"] == 100
    assert report["slate_already_graded_n"] == 1
    assert report["eligible_pregame_n"] == 1
    assert report["projected_graded_n"] == 101
    # No slate size was supplied, so coverage is unknown rather than passing.
    assert report["slate_coverage_verified"] is False
    assert report["blockers"] == ["NFL_FORWARD_SLATE_COVERAGE_UNVERIFIED"]
    assert report["status"] == "PROJECTED_TO_REACH_MINIMUM_COVERAGE_UNVERIFIED"


def test_expected_slate_larger_than_ledger_events_is_flagged_fail_closed():
    db = _ReadOnlyDB(predictions=[_prediction(f"slate-{i:02d}") for i in range(10)])

    report = project_forward_cohort(
        db, slate_date=SLATE, expected_slate_games=12, min_forward=100
    )

    assert report["observed_slate_games"] == 10
    assert report["unobserved_slate_games"] == 2
    assert "NFL_FORWARD_SLATE_GAMES_UNOBSERVED:2" in report["blockers"]
    assert report["projected_graded_n"] == 105
    # A projection never certifies even when the count is projected to pass.
    assert report["certification_recommendation"] == "DO_NOT_CERTIFY_YET"


def test_projected_minimum_cannot_hide_an_ungradeable_slate_game():
    # Reproduction: 99 current grades + one good + one irrevocably late
    # prediction satisfies the numeric minimum but not full-slate readiness.
    db = _ReadOnlyDB(
        graded=99,
        predictions=[
            _prediction("slate-good"),
            _prediction("slate-late", created="2026-11-15T19:00:00+00:00"),
        ],
    )
    report = project_forward_cohort(db, slate_date=SLATE, expected_slate_games=2)
    assert report["projected_graded_n"] == 100
    assert report["slate_coverage_verified"] is True
    assert report["cannot_grade_n"] == 1
    assert report["status"] == "HOLD_SLATE_EVENTS_CANNOT_GRADE"
    assert "NFL_FORWARD_SLATE_EVENTS_CANNOT_GRADE:1" in report["blockers"]
    assert report["certification_recommendation"] == "DO_NOT_CERTIFY_YET"
    assert report["can_execute"] is False


def test_observed_slate_exceeds_expected_is_a_coverage_hold():
    db = _ReadOnlyDB(graded=99, predictions=[_prediction("slate-1"), _prediction("slate-2")])
    report = project_forward_cohort(db, slate_date=SLATE, expected_slate_games=1)

    assert report["observed_slate_games"] == 2
    assert report["slate_coverage_verified"] is False
    assert "NFL_FORWARD_SLATE_COUNT_MISMATCH:2_GT_1" in report["blockers"]
    assert report["status"] == "PROJECTED_TO_REACH_MINIMUM_COVERAGE_UNVERIFIED"
    assert report["certification_recommendation"] == "DO_NOT_CERTIFY_YET"


def test_corrected_tie_is_not_still_counted_from_stale_persisted_grade():
    db = _ReadOnlyDB(graded=99, predictions=[_prediction("slate-tie")])
    # A previously counted outcome was corrected to a tie; persisted grade
    # rows are immutable audit history, but the grader excludes current ties.
    db.rows["wow_nfl_training_games"][0]["tie"] = True
    db.rows["wow_nfl_training_games"].append(
        {"game_id": "slate-tie", "home_score": 20, "away_score": 20,
         "home_win": None, "tie": True}
    )
    report = project_forward_cohort(db, slate_date=SLATE, expected_slate_games=1)

    assert report["graded_n"] == 98
    assert report["persisted_grade_n"] == 99
    assert report["current_cohort_reconciled_with_persisted_grades"] is False
    assert report["eligible_pregame_n"] == 0
    assert report["cannot_grade_n"] == 1
    assert report["slate_events"][0]["cannot_grade_reason"] == "SETTLED_TIE_EXCLUDED"
    assert report["projected_graded_n"] == 98
    assert report["status"] == "HOLD_PERSISTED_FORWARD_GRADE_DRIFT"
    assert report["certification_recommendation"] == "DO_NOT_CERTIFY_YET"


def test_unverified_slate_coverage_cannot_claim_the_minimum_is_reached():
    # Two predictions for a twelve-game slate: without an expected slate size the
    # projection must not read as ready just because two rows are enough.
    db = _ReadOnlyDB(graded=99, predictions=[_prediction("slate-1")])

    report = project_forward_cohort(db, slate_date=SLATE, min_forward=100)

    assert report["observed_slate_games"] == 1
    assert report["slate_coverage_verified"] is False
    assert report["unobserved_slate_games"] == 0
    assert "NFL_FORWARD_SLATE_COVERAGE_UNVERIFIED" in report["blockers"]
    assert report["status"] == "PROJECTED_TO_REACH_MINIMUM_COVERAGE_UNVERIFIED"
    assert report["certification_recommendation"] == "DO_NOT_CERTIFY_YET"


def test_invalid_row_outside_the_slate_holds_the_projection():
    # The grader validates every loaded prediction row, so an off-slate defect
    # still blocks the whole run even though this slate looks clean.
    off_slate = _prediction("off-slate", kickoff="2026-11-08T18:00:00+00:00")
    off_slate["calibrated_selection_probability"] = 1.0
    db = _ReadOnlyDB(
        predictions=[_prediction("slate-1"), off_slate]
    )

    report = project_forward_cohort(db, slate_date=SLATE, expected_slate_games=1)

    assert report["observed_slate_games"] == 1
    assert report["eligible_pregame_n"] == 1
    assert report["ledger_violation_n"] == 1
    assert report["ledger_violations"] == [{
        "official_event_id": "off-slate",
        "event_prediction_id": "pred-off-slate",
        "code": "MODEL_OUTPUT_INVALID:calibrated_selection_probability",
    }]
    assert report["status"] == "HOLD_PREDICTION_LEDGER_INVALID"
    assert "PREDICTION_LEDGER_WOULD_FAIL_GRADER:1" in report["blockers"]


def test_impossible_calendar_date_is_rejected_before_any_read(monkeypatch):
    request_runtime, _app = _installed_runtime(monkeypatch, _ReadOnlyDB())

    with pytest.raises(ValidationError):
        request_runtime.NflForwardCohortPreflightRequest(slate_date="2026-02-31")

    assert request_runtime.NflForwardCohortPreflightRequest(slate_date=SLATE).slate_date == SLATE
    assert request_runtime.NflForwardCohortPreflightRequest().slate_date is None


def _real_fastapi_app(monkeypatch, db, *, authorized):
    """Register the real route on a real FastAPI app with a real auth seam."""
    from fastapi import FastAPI

    import team_event_request_runtime as request_runtime

    def deny() -> None:
        raise HTTPException(status_code=401, detail={"code": "UNAUTHORIZED"})

    def allow() -> None:
        return None

    monkeypatch.setattr(request_runtime, "install_nfl_hydration_startup", lambda app, *, db_client_fn: None)
    monkeypatch.setattr(request_runtime, "install_nfl_model_startup", lambda app, *, db_client_fn: None)
    monkeypatch.setattr(request_runtime, "install_nfl_team_event_publication", lambda module: True)
    monkeypatch.setenv("WOW_V17_ACTIVE", "1")
    app = FastAPI()
    request_runtime.install_team_event_request_routes(
        app,
        auth_dependency=allow if authorized else deny,
        db_client_fn=lambda: db,
        event_api=object(),
    )
    return app


def test_registered_route_rejects_a_caller_that_fails_authentication(monkeypatch):
    from fastapi.testclient import TestClient

    app = _real_fastapi_app(monkeypatch, _ReadOnlyDB(), authorized=False)

    response = TestClient(app).post(PREFLIGHT_PATH, json={"slate_date": SLATE})

    assert response.status_code == 401


def test_registered_route_serves_readiness_when_authentication_passes(monkeypatch):
    from fastapi.testclient import TestClient

    db = _ReadOnlyDB(predictions=[_prediction("slate-1")])
    app = _real_fastapi_app(monkeypatch, db, authorized=True)

    response = TestClient(app).post(
        PREFLIGHT_PATH, json={"slate_date": SLATE, "expected_slate_games": 1}
    )

    assert response.status_code == 200
    body = response.json()
    assert body["graded_n"] == 95
    assert body["eligible_pregame_n"] == 1
    assert body["projection_is_certification_evidence"] is False
    assert body["certification_recommendation"] == "DO_NOT_CERTIFY_YET"
    assert body["can_execute"] is False


def test_assess_event_without_any_valid_pregame_row_states_the_reason():
    event = assess_slate_event(
        "only-late",
        [_prediction("only-late", created="2026-11-15T20:00:00+00:00")],
        already_graded=False,
    )
    assert event["status"] == "CANNOT_GRADE"
    assert event["cannot_grade_reason"] == "PREDICTION_ONLY_AFTER_KICKOFF"
    assert event["can_execute"] is False


@pytest.mark.parametrize("bad", [0, -1, True, 1.5])
def test_invalid_minimum_is_rejected(bad):
    db = _ReadOnlyDB()
    with pytest.raises(ValueError):
        project_forward_cohort(db, slate_date=SLATE, min_forward=bad)


class _RecordingApp:
    """Minimal FastAPI stand-in; keeps the handler per path."""

    def __init__(self):
        self.router = SimpleNamespace(routes=[])
        self.handlers = {}

    def post(self, path, **_kwargs):
        def decorator(fn):
            self.router.routes.append(SimpleNamespace(path=path))
            self.handlers[path] = fn
            return fn

        return decorator


PREFLIGHT_PATH = "/internal/v17/nfl-forward-cohort-preflight"


def _installed_runtime(monkeypatch, db):
    import team_event_request_runtime as request_runtime

    monkeypatch.setattr(request_runtime, "install_nfl_hydration_startup", lambda app, *, db_client_fn: None)
    monkeypatch.setattr(request_runtime, "install_nfl_model_startup", lambda app, *, db_client_fn: None)
    monkeypatch.setattr(request_runtime, "install_nfl_team_event_publication", lambda module: True)
    monkeypatch.setattr(request_runtime, "scout_route_auth_dependency", lambda dependency: dependency)
    monkeypatch.setenv("WOW_V17_ACTIVE", "1")
    app = _RecordingApp()
    request_runtime.install_team_event_request_routes(
        app, auth_dependency=object(), db_client_fn=lambda: db, event_api=object()
    )
    return request_runtime, app


def test_preflight_route_reports_readiness_without_authorizing(monkeypatch):
    db = _ReadOnlyDB(predictions=[_prediction("slate-1")])
    request_runtime, app = _installed_runtime(monkeypatch, db)

    report = app.handlers[PREFLIGHT_PATH](
        request_runtime.NflForwardCohortPreflightRequest(
            slate_date=SLATE, expected_slate_games=12
        )
    )

    assert report["graded_n"] == 95
    assert report["projected_graded_n"] == 96
    assert report["status"] == "PROJECTED_SHORT_OF_MINIMUM"
    assert "NFL_FORWARD_SLATE_GAMES_UNOBSERVED:11" in report["blockers"]
    assert report["projection_is_certification_evidence"] is False
    assert report["certification_recommendation"] == "DO_NOT_CERTIFY_YET"
    assert report["can_execute"] is False


def test_preflight_route_defaults_to_the_current_eastern_slate(monkeypatch):
    db = _ReadOnlyDB(predictions=[])
    request_runtime, app = _installed_runtime(monkeypatch, db)
    monkeypatch.setattr(request_runtime, "current_slate_date", lambda: date(2026, 11, 15))

    report = app.handlers[PREFLIGHT_PATH](None)

    assert report["slate_date"] == "2026-11-15"
    assert report["observed_slate_games"] == 0
    assert report["status"] == "PROJECTED_SHORT_OF_MINIMUM"
    assert report["can_execute"] is False


def test_preflight_route_fails_closed_without_authorizing(monkeypatch):
    def explode(_db, **_kwargs):
        raise RuntimeError("database unavailable")

    request_runtime, app = _installed_runtime(monkeypatch, _ReadOnlyDB(predictions=[]))
    monkeypatch.setattr(request_runtime, "project_forward_cohort", explode)

    with pytest.raises(HTTPException) as excinfo:
        app.handlers[PREFLIGHT_PATH](None)

    assert excinfo.value.status_code == 503
    assert excinfo.value.detail["code"] == "NFL_FORWARD_COHORT_PREFLIGHT_FAILED"
    assert excinfo.value.detail["projection_is_certification_evidence"] is False
    assert excinfo.value.detail["certification_recommendation"] == "DO_NOT_CERTIFY_YET"
    assert excinfo.value.detail["can_execute"] is False


@pytest.mark.parametrize("expected_slate_games", ["12.5", "0", "12"])
def test_workflow_reporting_failure_paths_warn_without_network(tmp_path, expected_slate_games):
    """Execute the actual embedded reporting Python with stubbed OIDC and HTTP 404."""
    workflow = (
        Path(__file__).resolve().parents[2]
        / ".github" / "workflows" / "wow-v17-nfl-forward-shadow.yml"
    ).read_text(encoding="utf-8")
    step = workflow.split("      - name: Report per-slate NFL forward-cohort readiness", 1)[1]
    block = step.split("          python - <<'PY'\n", 1)[1].split("\n          PY", 1)[0]
    script = "\n".join(line[10:] if line.startswith("          ") else line for line in block.splitlines())

    # No network requests are made. A valid expected count reaches the simulated
    # undeployed route (404); invalid numeric input fails inside the same HOLD path.
    stub = """
import io
import urllib.error
import urllib.request
def fake_urlopen(req, timeout=None):
    if "fake-oidc.invalid" in req.full_url:
        return io.BytesIO(b'{"value":"test-oidc-token"}')
    raise urllib.error.HTTPError(req.full_url, 404, "Not Found", None, None)
urllib.request.urlopen = fake_urlopen
"""
    summary = tmp_path / "report.md"
    env = {
        **os.environ,
        "EXPECTED_SLATE_GAMES": expected_slate_games,
        "GITHUB_STEP_SUMMARY": str(summary),
        "WOW_ACTION_ORIGIN": "https://undeployed.invalid",
        "ACTIONS_ID_TOKEN_REQUEST_URL": "https://fake-oidc.invalid/token",
        "ACTIONS_ID_TOKEN_REQUEST_TOKEN": "not-a-secret",
    }
    result = subprocess.run(
        [sys.executable, "-c", stub + "\n" + script],
        env=env, capture_output=True, text=True, timeout=8, check=False,
    )
    assert result.returncode == 0, result.stderr
    assert "::warning::NFL forward cohort readiness" in result.stdout
    assert "UNAVAILABLE / HOLD_COHORT_READINESS_UNOBSERVED" in summary.read_text()
    assert ("HTTPError" if expected_slate_games == "12" else "ValueError") in result.stdout


def test_daily_workflow_reports_slate_readiness_before_kickoff():
    workflow = (
        Path(__file__).resolve().parents[2]
        / ".github" / "workflows" / "wow-v17-nfl-forward-shadow.yml"
    ).read_text(encoding="utf-8")

    assert "- name: Report per-slate NFL forward-cohort readiness" in workflow
    assert "if: always()" in workflow
    assert PREFLIGHT_PATH in workflow
    assert "expected_slate_games" in workflow
    assert "America/New_York" in workflow
    assert "projection_is_certification_evidence" in workflow
    assert "DO_NOT_CERTIFY_YET" in workflow
    assert "GITHUB_STEP_SUMMARY" in workflow
    assert "WOW_ACTION_ORIGIN" in workflow
    # Reporting readiness must degrade to a warning, not fail completed grading.
    assert "HOLD_COHORT_READINESS_UNOBSERVED" in workflow
    assert "except (urllib.error.URLError, TimeoutError, ValueError, KeyError)" in workflow
    assert "slate_coverage_verified" in workflow
    # Fractional workflow_dispatch numbers must enter the existing warning/HOLD
    # handler instead of raising ValueError before the try/except.
    assert workflow.index("          try:") < workflow.index('body["expected_slate_games"] = int(expected)')
    assert "not expected.isascii() or not expected.isdecimal()" in workflow
