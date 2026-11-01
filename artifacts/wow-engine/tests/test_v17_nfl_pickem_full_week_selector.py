"""Regression: a Week 8 full-card run cannot silently omit TNF or MNF.

The existing date-selector API stays backward compatible. The explicit
season/week selector discovers the full canonical NFL week across all dates.
No probability, terminal authorization or wager execution is introduced here.
"""
from __future__ import annotations

import pytest

import v17.nfl_pickem_runtime as runtime


WEEK_8 = [
    ("2026-10-29", "CAR", "GB"),
    ("2026-11-01", "ARI", "DAL"),
    ("2026-11-01", "ATL", "TB"),
    ("2026-11-01", "BAL", "BUF"),
    ("2026-11-01", "CLE", "PIT"),
    ("2026-11-01", "IND", "JAX"),
    ("2026-11-01", "KC", "DEN"),
    ("2026-11-01", "LAC", "LA"),
    ("2026-11-01", "LV", "NYJ"),
    ("2026-11-01", "MIN", "DET"),
    ("2026-11-01", "NE", "MIA"),
    ("2026-11-01", "PHI", "WAS"),
    ("2026-11-01", "TEN", "CIN"),
    ("2026-11-02", "CHI", "SEA"),
]


def _row(gameday, away, home, *, week=8, season=2026):
    return {
        "game_id": f"{season}_{week:02d}_{away}_{home}",
        "gameday": gameday,
        "gametime": "20:15" if gameday != "2026-11-01" else "13:00",
        "away_team": away,
        "home_team": home,
        "season": season,
        "week": week,
        "game_type": "REG",
        "home_score": None,
        "away_score": None,
    }


def _snapshot(monkeypatch):
    rows = [_row(*values) for values in WEEK_8]
    # Adjacent date and same team combination in a different week must not leak.
    rows.append(_row("2026-11-05", "CAR", "GB", week=9))
    rows.append(_row("2025-11-01", "MIN", "DET", week=8, season=2025))
    # NFL postseason games must never join an ordinary pick'em week.
    postseason = _row("2026-11-01", "NYG", "SF")
    postseason["game_type"] = "POST"
    rows.append(postseason)
    snapshot = {
        "snapshot_id": "frozen-week8-schedule",
        "fetched_at": "2026-10-27T12:00:00+00:00",
    }
    monkeypatch.setattr(runtime, "_load_latest_schedule_snapshot", lambda db: (snapshot, rows))
    return snapshot


def test_week_selector_includes_thursday_sunday_monday_and_not_adjacent_week(monkeypatch):
    _snapshot(monkeypatch)
    request = runtime.NFLPickemBoardRequest(
        requested_season=2026, requested_week=8, expected_game_count=14
    )
    dates, timezone_name = runtime._validate_request(request)
    assert dates == ()
    assert timezone_name == "America/Chicago"
    snapshot, events, blocked = runtime._canonical_inventory(
        object(),
        requested_dates=dates,
        requested_season=request.requested_season,
        requested_week=request.requested_week,
    )
    expected = {f"2026_08_{away}_{home}" for _, away, home in WEEK_8}
    assert snapshot["snapshot_id"] == "frozen-week8-schedule"
    assert {event["official_event_id"] for event in events} == expected
    assert events[0]["official_event_id"] == "2026_08_CAR_GB"
    assert events[-1]["official_event_id"] == "2026_08_CHI_SEA"
    assert len(events) == 14
    assert not blocked


def test_full_week_scorer_attempts_every_event_even_when_all_models_held(monkeypatch):
    _snapshot(monkeypatch)
    attempted = []

    def held_score(event, **kwargs):
        attempted.append(event["official_event_id"])
        return {
            "official_event_id": event["official_event_id"],
            "candidate_envelope": {
                "official_event_id": event["official_event_id"],
                "sport": "NFL",
                "league": "NFL",
                "home_team": event["home_team"],
                "away_team": event["away_team"],
            },
            "sporting_probability_completed": False,
            "probability_publishable": False,
            "code": "MODEL_UNAVAILABLE",
            "blockers": ["MODEL_UNAVAILABLE"],
            "can_execute": False,
        }

    monkeypatch.setattr(runtime, "_score_event", held_score)
    monkeypatch.setattr(runtime, "model_invocation_limit", lambda: 12)
    monkeypatch.setattr(runtime, "_score_tiebreaker", lambda db, events: {
        "status": "UNAVAILABLE", "tiebreaker_publishable": False, "can_execute": False,
    })
    monkeypatch.setattr(runtime, "persist_row_detail", lambda db, *, run_id, rows: {
        "status": "PERSISTED",
        "detail_available": True,
        "rows_persisted": len(rows),
        "can_execute": False,
    })

    result = runtime.run_nfl_pickem_board(
        runtime.NFLPickemBoardRequest(
            requested_season=2026, requested_week=8, expected_game_count=14
        ),
        db_client_fn=lambda: object(),
        event_api=object(),
    )

    expected = {f"2026_08_{away}_{home}" for _, away, home in WEEK_8}
    assert set(attempted) == expected
    assert result["canonical_events_discovered"] == 14
    assert sum(batch["events_attempted"] for batch in result["batch_receipts"]) == 14
    assert result["expected_game_count"] == 14
    assert result["requested_week"] == 8
    assert result["requested_season"] == 2026
    assert result["submission_ready"] is False
    assert result["source_receipt_persistence"]["rows_persisted"] == 14
    assert result["can_execute"] is False


def test_week_selector_rejects_partial_or_ambiguous_selector():
    with pytest.raises(ValueError, match="PICKEM_SEASON_WEEK_SELECTOR_INCOMPLETE"):
        runtime._validate_request(
            runtime.NFLPickemBoardRequest(requested_week=8)
        )
    with pytest.raises(ValueError, match="PICKEM_SEASON_WEEK_SELECTOR_INCOMPLETE"):
        runtime._validate_request(
            runtime.NFLPickemBoardRequest(requested_season=2026)
        )
    with pytest.raises(ValueError, match="PICKEM_SEASON_WEEK_DATES_AMBIGUOUS"):
        runtime._validate_request(
            runtime.NFLPickemBoardRequest(
                requested_season=2026,
                requested_week=8,
                requested_slate_dates=["2026-11-01"],
            )
        )
    with pytest.raises(ValueError, match="PICKEM_REQUESTED_SLATE_DATES_INVALID"):
        runtime._validate_request(runtime.NFLPickemBoardRequest())
