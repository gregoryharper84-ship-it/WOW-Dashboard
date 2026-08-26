"""Offline regressions for the canonical Daily narrow moneyline scope."""
from __future__ import annotations

from unittest.mock import MagicMock, patch

from gate_engine import daily_moneyline_scope as scope


def _event(event_id: str, commence_time: str) -> dict:
    return {
        "id": event_id,
        "commence_time": commence_time,
        "home_team": "Chicago Cubs",
        "away_team": "St. Louis Cardinals",
        "bookmakers": [],
    }


def _candidate(row_id: str = "row-1") -> dict:
    return {
        "row_id": row_id,
        "sport": "MLB",
        "team": "Chicago Cubs",
        "opponent": "St. Louis Cardinals",
        "event_id": "event-1",
        "slate_date": "2026-08-20",
        "market_type": "h2h",
        # Canonical Daily identity; this is deliberately not MONEYLINE_V1.
        "player": "Chicago Cubs",
        "prop": "outright_winner",
        "side": "WIN",
        "line": 0.0,
        "_daily_scope_market_snapshot": {},
    }


def test_remaining_today_filter_uses_persisted_instant_and_local_date():
    events = [
        _event("future-today", "2026-08-20T23:00:00Z"),  # 18:00 Chicago
        _event("already-started", "2026-08-20T21:00:00Z"),  # 16:00 Chicago
        _event("tomorrow-local", "2026-08-21T05:30:00Z"),  # 00:30 Chicago
        {"id": "unknown-time", "home_team": "A", "away_team": "B"},
    ]
    kept, notes = scope.filter_remaining_today_events(
        events,
        run_date="2026-08-20",
        run_timezone="America/Chicago",
        scope_requested_at="2026-08-20T22:00:00Z",
    )
    assert [event["id"] for event in kept] == ["future-today"]
    assert any("already-started:EXCLUDED_ALREADY_STARTED" in note for note in notes)
    assert any("tomorrow-local:EXCLUDED_DATE_MISMATCH" in note for note in notes)
    assert any("unknown-time:EXCLUDED_NO_COMMENCE_TIME" in note for note in notes)


def test_discovery_only_uses_h2h_boundary_and_never_prop_acquisition():
    event = _event("event-1", "2026-08-20T23:00:00Z")
    with patch(
        "services.odds_api.get_h2h_odds",
        return_value=([event], "AVAILABLE"),
    ) as get_h2h:
        rows, status = scope.discover_remaining_today_moneyline(
            "MLB",
            run_date="2026-08-20",
            run_timezone="America/Chicago",
            scope_requested_at="2026-08-20T22:00:00Z",
        )
    get_h2h.assert_called_once_with("baseball_mlb")
    assert status["MLB_odds"] == "AVAILABLE"
    assert len(rows) == 2
    assert {row["market_type"] for row in rows} == {"h2h"}
    assert {row["prop"] for row in rows} == {"outright_winner"}
    assert {row["side"] for row in rows} == {"WIN"}


def test_discovery_normalizes_h2h_unavailability_to_fail_closed_status():
    with patch(
        "services.odds_api.get_h2h_odds",
        return_value=([], "FALLBACK_RUNDOWN:FAILED: upstream unavailable"),
    ):
        rows, status = scope.discover_remaining_today_moneyline(
            "MLB",
            run_date="2026-08-20",
            run_timezone="America/Chicago",
            scope_requested_at="2026-08-20T22:00:00Z",
        )
    assert rows == []
    assert status["MLB_odds"].startswith("FAILED:")


def test_discovery_normalizes_quota_proactive_skip_to_fail_closed_status():
    with patch(
        "services.odds_api.get_h2h_odds",
        return_value=([], "proactive_skip:paid:quota_exhausted"),
    ):
        rows, status = scope.discover_remaining_today_moneyline(
            "MLB",
            run_date="2026-08-20",
            run_timezone="America/Chicago",
            scope_requested_at="2026-08-20T22:00:00Z",
        )
    assert rows == []
    assert status["MLB_odds"] == (
        "FAILED:proactive_skip:paid:quota_exhausted"
    )


def test_unsupported_scoped_sport_is_explicitly_unavailable():
    rows, status = scope.discover_remaining_today_moneyline(
        "UNSUPPORTED",
        run_date="2026-08-20",
        run_timezone="America/Chicago",
        scope_requested_at="2026-08-20T22:00:00Z",
    )
    assert rows == []
    assert status["UNSUPPORTED_odds"].startswith("UNAVAILABLE:")


def test_scoped_scorer_removes_daily_identity_line_before_moneyline_validation():
    scorer = MagicMock(return_value={
        "terminal_label": "NO_PLAY",
        "blockers": ["EXISTING_LANE_BLOCKER"],
        "model_id": "MLB_MONEYLINE",
        "model_status": "ACTIVE",
        "probability_snapshot": {},
        "specialist_probability": {},
        "route_compatibility": {"compatibility": "PASS"},
    })
    with (
        patch(
            "gate_engine.moneyline_probability.score_outright_winner_row",
            scorer,
        ),
        patch(
            "gate_engine.moneyline.team_acquisition.acquire_team_data",
            return_value={},
        ),
    ):
        result = scope.score_scoped_moneyline_rows([_candidate()])

    scorer.assert_called_once()
    lane_row = scorer.call_args.args[0]
    assert "line" not in lane_row
    assert "prop" not in lane_row
    assert "side" not in lane_row
    assert result["no_play"][0]["line"] == 0.0
    assert result["no_play"][0]["terminal_label"] == "NO_PLAY"
    assert result["no_play"][0]["can_execute"] is False


def test_scoped_scorer_keeps_both_h2h_participants():
    home = _candidate("home")
    away = {
        **_candidate("away"),
        "team": "St. Louis Cardinals",
        "opponent": "Chicago Cubs",
        "player": "St. Louis Cardinals",
    }
    scorer = MagicMock(return_value={
        "terminal_label": "WATCH",
        "blockers": [],
        "model_id": "MLB_MONEYLINE",
        "model_status": "ACTIVE",
        "probability_snapshot": {},
        "specialist_probability": {},
        "route_compatibility": {"compatibility": "PASS"},
    })
    with (
        patch(
            "gate_engine.moneyline_probability.score_outright_winner_row",
            scorer,
        ),
        patch(
            "gate_engine.moneyline.team_acquisition.acquire_team_data",
            return_value={},
        ),
    ):
        result = scope.score_scoped_moneyline_rows([home, away])

    assert scorer.call_count == 2
    assert {card["team"] for card in result["watch"]} == {
        "Chicago Cubs",
        "St. Louis Cardinals",
    }