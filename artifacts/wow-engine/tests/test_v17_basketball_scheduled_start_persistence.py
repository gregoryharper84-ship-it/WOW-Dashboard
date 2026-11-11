"""Settled basketball rows must carry the validated tz-aware scheduled start (tip-off) in UTC."""
from datetime import datetime, timezone

import basketball_event_hydration_runtime as hydration
from test_v17_basketball_hydration_fallback import _espn_event, _sportsdataverse_row

RETRIEVED = "2026-09-22T19:00:00+00:00"


def _utc(text):
    dt = datetime.fromisoformat(text)
    assert dt.utcoffset() is not None and dt.utcoffset().total_seconds() == 0
    return dt


def test_espn_row_records_scheduled_start_utc():
    row = hydration.normalize_espn_game(_espn_event(), "WNBA", RETRIEVED)
    assert _utc(row["scheduled_start_utc"]) == datetime(2026, 9, 20, 23, 0, tzinfo=timezone.utc)


def test_sportsdataverse_row_converts_offset_tipoff_to_utc():
    row = hydration.normalize_sportsdataverse_game(_sportsdataverse_row(), "WNBA", RETRIEVED)
    assert _utc(row["scheduled_start_utc"]) == datetime(2026, 9, 20, 23, 0, tzinfo=timezone.utc)  # 19:00-04:00


def test_balldontlie_row_records_start_when_provided_and_null_otherwise():
    raw = {
        "id": 9, "status": "Final", "home_team_score": 100, "visitor_team_score": 90,
        "home_team": {"id": 1}, "visitor_team": {"id": 2}, "date": "2026-09-20",
        "datetime": "2026-09-20T23:00:00Z", "_wow_source_observed_at": "2026-09-22T18:00:00+00:00",
    }
    row = hydration.normalize_game(raw, "NBA", RETRIEVED)
    assert _utc(row["scheduled_start_utc"]) == datetime(2026, 9, 20, 23, 0, tzinfo=timezone.utc)
    del raw["datetime"]
    assert hydration.normalize_game(raw, "NBA", RETRIEVED)["scheduled_start_utc"] is None


def test_start_never_after_source_observation():
    for row in (
        hydration.normalize_espn_game(_espn_event(), "WNBA", RETRIEVED),
        hydration.normalize_sportsdataverse_game(_sportsdataverse_row(), "WNBA", RETRIEVED),
    ):
        assert _utc(row["scheduled_start_utc"]) < _utc(row["source_retrieved_at"])
