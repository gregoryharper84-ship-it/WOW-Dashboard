from datetime import datetime, timezone

import pytest

from v17 import nfl_feature_table_refresh as refresh
from v17 import nfl_forward_shadow as shadow
from v17 import nfl_team_event_specialist as spec


# --- identity: persisted names are always full team names ----------------------

@pytest.mark.parametrize("raw, full", [
    ("PIT", "Pittsburgh Steelers"), ("was", "Washington Commanders"), ("SF", "San Francisco 49ers"),
    ("LA", "Los Angeles Rams"), ("Buffalo Bills", "Buffalo Bills"), ("  KC ", "Kansas City Chiefs"),
])
def test_display_team_expands_abbreviations_and_keeps_full_names(raw, full):
    assert spec._display_team(raw) == full


def test_inverse_map_is_complete_one_to_one_and_round_trips():
    assert len(spec.NFL_ABBREVIATION_TO_TEAM_NAME) == 32
    for code, name in spec.NFL_ABBREVIATION_TO_TEAM_NAME.items():
        assert spec._canonical_team(name) == code
        assert spec._canonical_team(code) == code


def test_unknown_value_passes_through_unchanged():
    assert spec._display_team("Springfield Atoms") == "Springfield Atoms"


# --- feature table refresh ------------------------------------------------------

def test_refresh_rebuilds_only_requested_seasons_with_shared_builder():
    calls = {"summary": [], "upsert": None}
    games = [{"game_id": "a", "season": 2025}, {"game_id": "b", "season": 2026}]

    def builder(g, s):
        assert g is games and s == ["summ"]
        return [
            {"game_id": "a", "season": 2025, "target_outcome": "HOME_WIN"},
            {"game_id": "b", "season": 2026, "target_outcome": "AWAY_WIN"},
            {"game_id": "c", "season": 2026, "target_outcome": None},
        ]

    def upsert(db, table, rows, conflict):
        calls["upsert"] = (table, [r["game_id"] for r in rows], conflict)
        return len(rows)

    out = refresh.refresh_feature_rows(
        object(), seasons=[2026],
        summary_refresh_fn=lambda db, season: calls["summary"].append(season) or {"season": season},
        history_loader=lambda db: (games, ["summ"]), builder=builder, upsert=upsert,
    )
    assert calls["summary"] == [2026]
    assert calls["upsert"] == ("wow_nfl_pregame_feature_rows", ["b", "c"], "game_id")
    assert out["feature_rows_built"] == 2 and out["feature_rows_labeled"] == 1
    assert out["can_execute"] is False


def test_refresh_requires_a_season():
    with pytest.raises(ValueError):
        refresh.refresh_feature_rows(object(), seasons=[])


@pytest.mark.parametrize("now, season", [
    (datetime(2026, 10, 9, tzinfo=timezone.utc), 2026),
    (datetime(2027, 2, 3, tzinfo=timezone.utc), 2026),
    (datetime(2027, 3, 1, tzinfo=timezone.utc), 2027),
])
def test_active_nfl_season(now, season):
    assert shadow.active_nfl_season(now) == season


def test_daily_job_refreshes_active_season_only(monkeypatch):
    monkeypatch.setattr(shadow, "active_nfl_season", lambda now=None: 2026)
    seen = []
    out = shadow._refresh_feature_table(object(), {"seasons": [2025, 2026]}, lambda db, seasons: seen.append(seasons) or {"status": "COMPLETED"})
    assert seen == [[2026]] and out["status"] == "COMPLETED"


def test_daily_job_skips_when_active_season_not_settled(monkeypatch):
    monkeypatch.setattr(shadow, "active_nfl_season", lambda now=None: 2026)
    out = shadow._refresh_feature_table(object(), {"seasons": [2024, 2025]}, lambda *a, **k: pytest.fail("ran"))
    assert out["status"] == "SKIPPED"


def test_feature_refresh_failure_is_typed_and_never_breaks_grading(monkeypatch):
    monkeypatch.setattr(shadow, "active_nfl_season", lambda now=None: 2026)

    def boom(db, seasons):
        raise TimeoutError("pbp download")

    out = shadow._refresh_feature_table(object(), {"seasons": [2026]}, boom)
    assert out == {"status": "FAILED", "reason_code": "NFL_FEATURE_TABLE_REFRESH_TIMEOUTERROR", "can_execute": False}


def test_run_forward_shadow_reports_feature_refresh(monkeypatch):
    monkeypatch.setattr(shadow, "load_prediction_rows", lambda db: [])
    monkeypatch.setattr(shadow, "select_canonical_forward_predictions", lambda rows: [])
    monkeypatch.setattr(shadow, "load_outcome_rows", lambda db: [])
    monkeypatch.setattr(shadow, "grade_forward_predictions", lambda c, o: [])
    monkeypatch.setattr(shadow, "persist_new_grades", lambda db, g: 0)
    monkeypatch.setattr(shadow, "calibration_health", lambda g, min_forward: {"status": "X"})
    monkeypatch.setattr(shadow, "persist_health", lambda db, h: None)
    monkeypatch.setattr(shadow, "active_nfl_season", lambda now=None: 2026)
    out = shadow.run_forward_shadow(
        object(), settlement_refresh_fn=lambda db: {"seasons": [2025, 2026]},
        feature_refresh_fn=lambda db, seasons: {"status": "COMPLETED", "seasons": seasons},
    )
    assert out["feature_table_refresh"] == {"status": "COMPLETED", "seasons": [2026]}
    assert out["status"] == "COMPLETED"
