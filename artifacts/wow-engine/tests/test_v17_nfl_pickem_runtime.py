from __future__ import annotations

from fastapi import FastAPI, HTTPException
import pytest

import v17.nfl_pickem_runtime as runtime
from v17.nfl_pickem_pool_optimizer import PICKEM_BOARD_READY

MATCHUPS = [
    ("2026-10-01", "PIT", "CLE"),
    ("2026-10-04", "IND", "WAS"),
    ("2026-10-04", "NE", "BUF"),
    ("2026-10-04", "NYJ", "CHI"),
    ("2026-10-04", "JAX", "CIN"),
    ("2026-10-04", "ARI", "NYG"),
    ("2026-10-04", "LA", "PHI"),
    ("2026-10-04", "GB", "TB"),
    ("2026-10-04", "TEN", "BAL"),
    ("2026-10-04", "DAL", "HOU"),
    ("2026-10-04", "MIA", "MIN"),
    ("2026-10-04", "KC", "LV"),
    ("2026-10-04", "DEN", "SF"),
    ("2026-10-04", "LAC", "SEA"),
    ("2026-10-04", "DET", "CAR"),
    ("2026-10-05", "ATL", "NO"),
]


def _schedule_rows():
    rows = []
    for index, (gameday, away, home) in enumerate(MATCHUPS, 1):
        if gameday == "2026-10-01":
            gametime = "20:15"
        elif gameday == "2026-10-05":
            gametime = "20:15"
        elif index == 2:
            gametime = "09:30"
        else:
            gametime = "13:00"
        rows.append(
            {
                "game_id": f"2026_04_{away}_{home}",
                "gameday": gameday,
                "gametime": gametime,
                "away_team": away,
                "home_team": home,
                "season": "2026",
                "week": "4",
                "game_type": "REG",
                "home_score": "",
                "away_score": "",
            }
        )
    return rows


def test_pregame_detection_does_not_treat_numeric_zero_as_missing_score():
    assert runtime._pregame_schedule_row({"home_score": None, "away_score": None}) is True
    assert runtime._pregame_schedule_row({"home_score": "", "away_score": ""}) is True
    assert runtime._pregame_schedule_row({"home_score": 0, "away_score": 0}) is False
    assert runtime._pregame_schedule_row({"home_score": "0", "away_score": "0"}) is False


def _governed_result(req, *, hold: bool = False):
    event_id = str(req.official_event_id)
    index = next(i for i, row in enumerate(_schedule_rows(), 1) if row["game_id"] == event_id)
    home_p = 0.51 + (index % 8) * 0.025
    away_p = 1.0 - home_p
    selected = req.home_team if home_p >= 0.5 else req.away_team
    opponent = req.away_team if selected == req.home_team else req.home_team
    selected_p = home_p if selected == req.home_team else away_p
    return {
        "event_prediction_id": f"pred-{event_id}",
        "model_version": "NFL_CHAMPION_TEST",
        "model_timestamp": "2026-09-30T13:00:00+00:00",
        "calibration_method": "TEST_CALIBRATOR",
        "calibration_version": "TEST_V1",
        "calibrated_home_probability": home_p,
        "calibrated_away_probability": away_p,
        "calibrated_home_lower_bound": max(0.0, home_p - 0.04),
        "calibrated_home_upper_bound": min(1.0, home_p + 0.04),
        "calibrated_away_lower_bound": max(0.0, away_p - 0.04),
        "calibrated_away_upper_bound": min(1.0, away_p + 0.04),
        "calibrated_selection_probability": selected_p,
        "rank_calibrated_lower_bound": max(0.0, selected_p - 0.04),
        "selected_participant": selected,
        "opponent": opponent,
        "sporting_probability_completed": True,
        "sporting_probability_status": "COMPLETED",
        "probability_fields_withheld": False,
        "model_probability_available": True,
        "probability_publishable": not hold,
        "rank_eligible": not hold,
        "terminal_label": "MODEL_QUALIFIED_HOLD" if hold else "FINAL_APPROVED",
        "global_terminal_authority": "V17_TERMINAL_REDUCER",
        "code": "LLP_EVENT_GOVERNANCE_NOT_PROVEN" if hold else "GOVERNED_PROBABILITY_PUBLISHED",
        "blockers": ["LLP_EVENT_DECISION_GOVERNOR_NOT_PROVEN"] if hold else [],
        "can_execute": False,
    }


def test_week_runtime_uses_canonical_schedule_scores_all_16_in_bounded_batches(monkeypatch):
    schedule_rows = _schedule_rows()
    snapshot = {
        "snapshot_id": "nfl-schedule-snapshot",
        "fetched_at": "2026-09-30T12:55:00+00:00",
        "content_sha256": "abc123",
    }
    monkeypatch.setattr(runtime, "_load_latest_schedule_snapshot", lambda db: (snapshot, schedule_rows))
    monkeypatch.setattr(runtime, "model_invocation_limit", lambda: 12)

    calls = []
    held_event_id = schedule_rows[4]["game_id"]

    def scorer(req, *, event_api, canonical_hydration_required=False):
        calls.append((req.official_event_id, canonical_hydration_required))
        return _governed_result(req, hold=str(req.official_event_id) == held_event_id)

    monkeypatch.setattr(runtime.team_runtime, "score_team_event_request", scorer)
    persisted = {}

    def persist(db, *, run_id, rows):
        persisted["run_id"] = run_id
        persisted["rows"] = rows
        return {
            "status": "PERSISTED",
            "detail_available": True,
            "rows_persisted": len(rows),
            "blockers": [],
            "can_execute": False,
        }

    monkeypatch.setattr(runtime, "persist_row_detail", persist)

    req = runtime.NFLPickemBoardRequest(
        requested_slate_dates=["2026-10-01", "2026-10-04", "2026-10-05"],
        requested_timezone="America/Chicago",
        expected_game_count=16,
    )
    out = runtime.run_nfl_pickem_board(
        req,
        db_client_fn=lambda: object(),
        event_api=object(),
    )

    assert out["status"] == PICKEM_BOARD_READY
    assert out["submission_ready"] is True
    assert out["canonical_events_discovered"] == 16
    assert out["model_invocation_batch_limit"] == 12
    assert out["batch_count"] == 2
    assert [batch["events_attempted"] for batch in out["batch_receipts"]] == [12, 4]
    assert out["batch_receipts"][0]["continuation_required"] is True
    assert out["batch_receipts"][1]["continuation_required"] is False
    assert len(calls) == 16
    assert all(canonical is True for _, canonical in calls)
    assert out["board"]["ready_pick_count"] == 16
    assert out["board"]["blocked_event_count"] == 0
    assert any(
        pick["source_terminal_label"] == "MODEL_QUALIFIED_HOLD"
        and pick["source_probability_publishable"] is False
        and pick["source_terminal_upgraded"] is False
        for pick in out["board"]["picks"]
    )
    assert persisted["run_id"] == out["run_id"]
    assert len(persisted["rows"]) == 16
    assert all(row["lane"] == "MONEYLINE" for row in persisted["rows"])
    assert out["source_receipt_persistence"]["rows_persisted"] == 16
    assert out["tiebreaker"]["status"] == "UNAVAILABLE"
    assert out["can_execute"] is False


def test_runtime_preserves_typed_scorer_failure_and_marks_board_incomplete(monkeypatch):
    snapshot = {"snapshot_id": "snap", "fetched_at": "2026-09-30T12:00:00+00:00"}
    rows = _schedule_rows()[:2]
    monkeypatch.setattr(runtime, "_load_latest_schedule_snapshot", lambda db: (snapshot, rows))
    monkeypatch.setattr(runtime, "model_invocation_limit", lambda: 12)

    def scorer(req, *, event_api, canonical_hydration_required=False):
        if str(req.official_event_id) == rows[1]["game_id"]:
            raise HTTPException(
                status_code=503,
                detail={
                    "code": "MODEL_SCORER_FAILED",
                    "blockers": ["NFL_FITTED_SCORER_FAILED"],
                    "can_execute": False,
                },
            )
        return _governed_result(req)

    monkeypatch.setattr(runtime.team_runtime, "score_team_event_request", scorer)
    monkeypatch.setattr(
        runtime,
        "persist_row_detail",
        lambda db, *, run_id, rows: {
            "status": "PERSISTED",
            "detail_available": True,
            "rows_persisted": len(rows),
            "blockers": [],
            "can_execute": False,
        },
    )
    req = runtime.NFLPickemBoardRequest(
        requested_slate_dates=["2026-10-01", "2026-10-04"],
        expected_game_count=2,
    )
    out = runtime.run_nfl_pickem_board(req, db_client_fn=lambda: object(), event_api=object())
    assert out["submission_ready"] is False
    assert out["board"]["ready_pick_count"] == 1
    assert out["board"]["blocked_event_count"] == 1
    assert out["board"]["blocked"][0]["source_model_status"] == "MODEL_SCORER_FAILED"
    assert "NFL_FITTED_SCORER_FAILED" in out["board"]["blocked"][0]["blockers"]


def test_runtime_request_rejects_invalid_date_timezone_or_strategy():
    with pytest.raises(ValueError, match="PICKEM_REQUESTED_SLATE_DATE_INVALID"):
        runtime._validate_request(
            runtime.NFLPickemBoardRequest(requested_slate_dates=["10/04/2026"])
        )
    with pytest.raises(ValueError, match="PICKEM_REQUESTED_TIMEZONE_INVALID"):
        runtime._validate_request(
            runtime.NFLPickemBoardRequest(
                requested_slate_dates=["2026-10-04"],
                requested_timezone="Not/A_Zone",
            )
        )
    with pytest.raises(ValueError, match="PICKEM_STRATEGY_MODE_UNSUPPORTED"):
        runtime._validate_request(
            runtime.NFLPickemBoardRequest(
                requested_slate_dates=["2026-10-04"],
                strategy_mode="POOL_WIN_EQUITY",
            )
        )


def test_route_installs_once_with_stable_operation_id():
    app = FastAPI()

    def auth():
        return True

    assert runtime.install_nfl_pickem_routes(
        app,
        auth_dependency=auth,
        db_client_fn=lambda: object(),
        event_api=object(),
    ) is True
    assert runtime.install_nfl_pickem_routes(
        app,
        auth_dependency=auth,
        db_client_fn=lambda: object(),
        event_api=object(),
    ) is True

    routes = [route for route in app.routes if getattr(route, "path", None) == runtime.ROUTE_PATH]
    assert len(routes) == 1
    assert routes[0].operation_id == runtime.OPERATION_ID
    assert "POST" in routes[0].methods
