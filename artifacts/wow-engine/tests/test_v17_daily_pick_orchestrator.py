from __future__ import annotations

from datetime import datetime, timedelta, timezone

from v17.daily_pick_orchestrator import DailyPicksRequest, compile_request, run_daily_picks


def _future(hours: int = 4) -> str:
    return (datetime.now(timezone.utc) + timedelta(hours=hours)).isoformat()


def _past(hours: int = 1) -> str:
    return (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()


def _moneyline_row(event_id: str, *, lower: float, calibrated: float, raw: float, status: str = "COMPLETED", code: str | None = None, start: str | None = None):
    result = {
        "raw_model_probability": raw,
        "calibrated_probability": calibrated,
        "calibrated_lower_bound": lower,
        "probability_publishable": status == "COMPLETED",
        "rank_eligible": status == "COMPLETED",
        "can_execute": False,
    }
    if code:
        result["code"] = code
    return {
        "lane": "MONEYLINE",
        "identity": {
            "official_event_id": event_id,
            "event_start_time": start or _future(),
            "sport": "MLB",
            "home_team": f"HOME-{event_id}",
            "away_team": f"AWAY-{event_id}",
            "snapshot_id": f"snap-{event_id}",
        },
        "result": result,
        "row_status": status,
    }


def _prop_row(event_id: str, outcomes: list[dict]):
    publishable = any(
        (outcome.get("payload") or {}).get("probability_publishable") is True
        and (outcome.get("payload") or {}).get("rank_eligible") is True
        for outcome in outcomes
    )
    return {
        "lane": "PROPS",
        "identity": {
            "event_id": event_id,
            "event_start_time": _future(),
            "sport": "MLB",
            "player": "Pitcher A",
            "stat_type": "strikeouts",
            "line": 4.5,
            "source_snapshot_id": f"prop-snap-{event_id}",
        },
        "result": {
            "outcomes": outcomes,
            "probability_publishable": publishable,
            "rank_eligible": publishable,
            "can_execute": False,
        },
        "row_status": "COMPLETED" if any(outcome.get("status") == "COMPLETED" for outcome in outcomes) else "HELD",
    }


def _prop_outcome(direction: str, *, status: str, lower: float | None = None, code: str | None = None):
    payload = {
        "probability_publishable": status == "COMPLETED",
        "rank_eligible": status == "COMPLETED",
        "can_execute": False,
    }
    if lower is not None:
        payload.update({
            "raw_model_probability": min(lower + 0.08, 0.99),
            "calibrated_probability": min(lower + 0.05, 0.99),
            "calibrated_probability_lower_bound": lower,
        })
    if code:
        payload["code"] = code
    return {"direction": direction, "status": status, "payload": payload}


def _snapshot(rows, blockers=None, run_id="snapshot-1", requested_lanes=None):
    return {
        "run_id": run_id,
        "run_status": "COMPLETED",
        "requested_lanes": requested_lanes or ["MONEYLINE"],
        "rows": rows,
        "blockers": blockers or [],
        "lane_reconciliation": {},
        "can_execute": False,
    }


def test_compiler_turns_terse_request_into_governed_daily_contract():
    compiled = compile_request(DailyPicksRequest(query="Give me the best picks remaining today"))
    assert compiled["normalized_intent"] == "BEST_PICKS_REMAINING_TODAY"
    assert compiled["ranking_metric"] == "calibrated_lower_bound"
    assert compiled["scout_intake_required"] is True
    assert compiled["scout_is_additive"] is True
    assert compiled["final_refresh_required"] is True
    assert compiled["exact_once_reconciliation"] is True
    assert compiled["can_execute"] is False


def test_rank_is_governed_lower_bound_not_raw_or_point_probability():
    rows = [
        _moneyline_row("A", lower=0.61, calibrated=0.90, raw=0.94),
        _moneyline_row("B", lower=0.70, calibrated=0.78, raw=0.80),
    ]
    result = run_daily_picks(
        DailyPicksRequest(lanes=["MONEYLINE"], requested_count=2, transient_retry_attempts=0),
        db=object(), market_api=object(), event_api=object(),
        snapshot_runner=lambda *_args, **_kwargs: _snapshot(rows),
    )
    assert [row["identity"]["official_event_id"] for row in result["leaderboard"]] == ["B", "A"]
    assert result["ranking_metric"] == "calibrated_lower_bound"
    assert result["can_execute"] is False


def test_isolated_scorer_failure_does_not_abort_other_candidates_and_retries_once():
    calls = {"n": 0}

    def runner(*_args, **_kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            return _snapshot([
                _moneyline_row("GOOD", lower=0.66, calibrated=0.72, raw=0.74),
                _moneyline_row("RETRY", lower=0.0, calibrated=0.0, raw=0.0, status="HELD", code="MODEL_SCORER_FAILED"),
            ], run_id="snapshot-1")
        return _snapshot([
            _moneyline_row("GOOD", lower=0.66, calibrated=0.72, raw=0.74),
            _moneyline_row("RETRY", lower=0.64, calibrated=0.70, raw=0.73),
        ], run_id="snapshot-2")

    result = run_daily_picks(
        DailyPicksRequest(lanes=["MONEYLINE"], requested_count=5, transient_retry_attempts=1),
        db=object(), market_api=object(), event_api=object(), snapshot_runner=runner,
    )
    assert calls["n"] == 2
    assert result["reconciliation"]["retry_attempts_used"] == 1
    assert result["reconciliation"]["balanced"] is True
    assert {row["identity"]["official_event_id"] for row in result["leaderboard"]} == {"GOOD", "RETRY"}


def test_prop_retry_merges_each_direction_without_erasing_other_side():
    calls = {"n": 0}

    def runner(*_args, **_kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            rows = [_prop_row("P1", [
                _prop_outcome("MORE", status="COMPLETED", lower=0.67),
                _prop_outcome("LESS", status="HELD", code="MODEL_SCORER_FAILED"),
            ])]
        else:
            rows = [_prop_row("P1", [
                _prop_outcome("MORE", status="HELD", code="MODEL_SCORER_FAILED"),
                _prop_outcome("LESS", status="COMPLETED", lower=0.62),
            ])]
        return _snapshot(rows, run_id=f"snapshot-{calls['n']}", requested_lanes=["PROPS"])

    result = run_daily_picks(
        DailyPicksRequest(lanes=["PROPS"], requested_count=5, transient_retry_attempts=1),
        db=object(), market_api=object(), event_api=object(), snapshot_runner=runner,
    )
    assert calls["n"] == 2
    assert result["reconciliation"]["rank_eligible"] == 2
    assert {row["direction"] for row in result["leaderboard"]} == {"MORE", "LESS"}
    assert result["coverage"]["cross_sport_moneyline_complete"] is None
    assert result["run_status"] == "COMPLETED"


def test_model_unavailable_is_preserved_and_not_retried():
    calls = {"n": 0}

    def runner(*_args, **_kwargs):
        calls["n"] += 1
        return _snapshot([
            _moneyline_row("UNAVAILABLE", lower=0.0, calibrated=0.0, raw=0.0, status="HELD", code="MODEL_UNAVAILABLE")
        ])

    result = run_daily_picks(
        DailyPicksRequest(lanes=["MONEYLINE"], transient_retry_attempts=1),
        db=object(), market_api=object(), event_api=object(), snapshot_runner=runner,
    )
    assert calls["n"] == 1
    assert result["blocked_summary"]["MODEL_UNAVAILABLE"] == 1
    assert result["leaderboard"] == []


def test_event_that_started_during_run_is_removed_at_publication_refresh():
    rows = [_moneyline_row("STARTED", lower=0.82, calibrated=0.86, raw=0.88, start=_past())]
    result = run_daily_picks(
        DailyPicksRequest(lanes=["MONEYLINE"], transient_retry_attempts=0),
        db=object(), market_api=object(), event_api=object(),
        snapshot_runner=lambda *_args, **_kwargs: _snapshot(rows),
    )
    assert result["leaderboard"] == []
    assert result["blocked_summary"]["EVENT_ALREADY_STARTED"] == 1
    terminal = result["diagnostics"]["terminal_candidate_rows"][0]
    assert terminal["rank_eligible"] is False
    assert terminal["final_refresh"] == "EVENT_ALREADY_STARTED"


def test_exact_once_reconciliation_never_silently_drops_terminal_candidates():
    rows = [
        _moneyline_row("A", lower=0.70, calibrated=0.75, raw=0.77),
        _moneyline_row("B", lower=0.0, calibrated=0.0, raw=0.0, status="HELD", code="MODEL_INPUTS_INSUFFICIENT"),
        _moneyline_row("C", lower=0.0, calibrated=0.0, raw=0.0, status="HELD", code="MODEL_OUTPUT_INVALID"),
    ]
    result = run_daily_picks(
        DailyPicksRequest(lanes=["MONEYLINE"], transient_retry_attempts=0),
        db=object(), market_api=object(), event_api=object(),
        snapshot_runner=lambda *_args, **_kwargs: _snapshot(rows),
    )
    reconciliation = result["reconciliation"]
    assert reconciliation["terminal_candidates"] == 3
    assert reconciliation["rank_eligible"] == 1
    assert reconciliation["blocked_or_purged"] == 2
    assert reconciliation["balanced"] is True
    assert reconciliation["no_silent_candidate_loss"] is True


def test_cross_sport_incompleteness_is_explicit_not_silently_claimed_complete():
    result = run_daily_picks(
        DailyPicksRequest(lanes=["MONEYLINE"], transient_retry_attempts=0),
        db=object(), market_api=object(), event_api=object(),
        snapshot_runner=lambda *_args, **_kwargs: _snapshot([]),
    )
    assert result["coverage"]["cross_sport_moneyline_complete"] is False
    assert result["coverage"]["cross_sport_blocker"] == "CROSS_SPORT_DISCOVERY_ADAPTERS_NOT_REGISTERED_IN_DAILY_SNAPSHOT_RUNTIME"
    assert result["run_status"] == "COMPLETED_WITH_BLOCKERS"
