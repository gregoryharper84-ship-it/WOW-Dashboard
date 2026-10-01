from __future__ import annotations

from threading import Lock
from time import sleep

import v17.nfl_pickem_runtime as runtime


def _event(index: int) -> dict:
    return {
        "official_event_id": f"2026_04_A{index}_H{index}",
        "gameday": "2026-10-04",
        "event_start_time_utc": f"2026-10-04T{17 + (index % 4):02d}:00:00+00:00",
        "home_team": f"H{index}",
        "away_team": f"A{index}",
        "week": 4,
        "season": 2026,
        "game_type": "REG",
        "can_execute": False,
    }


def _governed(req) -> dict:
    return {
        "event_prediction_id": f"pred-{req.official_event_id}",
        "model_version": "NFL_CHAMPION_TEST",
        "model_timestamp": "2026-10-01T12:00:00+00:00",
        "calibration_method": "TEST_CALIBRATOR",
        "calibration_version": "TEST_V1",
        "calibrated_home_probability": 0.60,
        "calibrated_away_probability": 0.40,
        "calibrated_home_lower_bound": 0.55,
        "calibrated_home_upper_bound": 0.65,
        "calibrated_away_lower_bound": 0.35,
        "calibrated_away_upper_bound": 0.45,
        "calibrated_selection_probability": 0.60,
        "rank_calibrated_lower_bound": 0.55,
        "selected_participant": req.home_team,
        "opponent": req.away_team,
        "sporting_probability_completed": True,
        "sporting_probability_status": "COMPLETED",
        "probability_fields_withheld": False,
        "model_probability_available": True,
        "probability_publishable": True,
        "rank_eligible": True,
        "terminal_label": "FINAL_APPROVED",
        "global_terminal_authority": "V17_TERMINAL_REDUCER",
        "code": "GOVERNED_PROBABILITY_PUBLISHED",
        "blockers": [],
        "can_execute": False,
    }


def test_pickem_separates_batch_size_from_scorer_concurrency(monkeypatch):
    events = [_event(index) for index in range(8)]
    snapshot = {
        "snapshot_id": "46196e13-b806-44fc-b06b-a355f3bc125a",
        "fetched_at": "2026-10-01T12:00:00+00:00",
    }
    monkeypatch.setattr(
        runtime,
        "_canonical_inventory",
        lambda db, *, requested_dates: (snapshot, events, []),
    )
    monkeypatch.setattr(runtime, "model_invocation_limit", lambda: 12)
    monkeypatch.setattr(runtime, "_scorer_concurrency_limit", lambda: 2)
    monkeypatch.setattr(
        runtime,
        "_score_tiebreaker",
        lambda db, rows: {
            "status": "UNAVAILABLE",
            "tiebreaker_publishable": False,
            "can_execute": False,
        },
    )
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

    guard = Lock()
    active = 0
    max_active = 0
    calls = []

    def scorer(req, *, event_api, canonical_hydration_required=False):
        nonlocal active, max_active
        with guard:
            active += 1
            max_active = max(max_active, active)
            calls.append(req.official_event_id)
        try:
            sleep(0.02)
            return _governed(req)
        finally:
            with guard:
                active -= 1

    monkeypatch.setattr(runtime.team_runtime, "score_team_event_request", scorer)

    out = runtime.run_nfl_pickem_board(
        runtime.NFLPickemBoardRequest(
            requested_slate_dates=["2026-10-04"],
            expected_game_count=8,
        ),
        db_client_fn=lambda: object(),
        event_api=object(),
    )

    assert out["model_invocation_batch_limit"] == 12
    assert out["scorer_concurrency_limit"] == 2
    assert out["batch_receipts"][0]["scorer_concurrency_limit"] == 2
    assert len(calls) == 8
    assert max_active == 2
    assert out["board"]["ready_pick_count"] == 8
    assert out["board"]["blocked_event_count"] == 0
    assert out["can_execute"] is False


def test_raw_scorer_transport_exception_is_not_relabelled_model_failure(monkeypatch):
    class RemoteProtocolError(Exception):
        pass

    def scorer(req, *, event_api, canonical_hydration_required=False):
        raise RemoteProtocolError("server disconnected")

    monkeypatch.setattr(runtime.team_runtime, "score_team_event_request", scorer)
    result = runtime._score_event(
        _event(1),
        run_id="nfl-pickem-test",
        requested_timezone="America/Chicago",
        snapshot={
            "snapshot_id": "46196e13-b806-44fc-b06b-a355f3bc125a",
            "fetched_at": "2026-10-01T12:00:00+00:00",
        },
        event_api=object(),
    )

    assert result["code"] == "TRANSPORT_FAILURE"
    assert result["error_type"] == "RemoteProtocolError"
    assert result["blockers"] == ["PICKEM_TEAM_EVENT_TRANSPORT_FAILURE"]
    assert result["probability_publishable"] is False
    assert result["rank_eligible"] is False
    assert result["can_execute"] is False
