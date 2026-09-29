from datetime import datetime, timedelta, timezone

from v17.nfl_spread_context_challenger import (
    AUTOMATIC_CERTIFICATION,
    AUTOMATIC_PROMOTION,
    CAN_EXECUTE,
    FEATURE_SCHEMA_VERSION,
    MODEL_FAMILY,
    PROBABILITY_PUBLISHABLE,
    build_margin_rows,
)


def _event(season, week, day, home, away, hs, as_):
    return {
        "event_id": f"NFL:{season}_{week:02d}_{away}_{home}",
        "event_start_time": datetime(season, 9, 1, 12, tzinfo=timezone.utc).isoformat()
        if day == 0 else (datetime(season, 9, 1, 12, tzinfo=timezone.utc) + timedelta(days=day)).isoformat(),
        "season": season,
        "week": week,
        "home_team": home,
        "away_team": away,
        "home_score": hs,
        "away_score": as_,
        "home_process_margin": float(hs - as_),
        "away_process_margin": float(as_ - hs),
        "home_turnovers": 1.0,
        "away_turnovers": 1.0,
        "home_sacks_allowed": 2.0,
        "away_sacks_allowed": 2.0,
        "home_special_teams_epa": 0.1,
        "away_special_teams_epa": -0.1,
        "home_history_lineup_ids": [f"QB-{home}"],
        "away_history_lineup_ids": [f"QB-{away}"],
        "source_manifest": {"source": "TEST"},
    }


def test_spread_v2_is_research_only_and_market_independent():
    assert MODEL_FAMILY == "NFL_SPREAD_CONTEXT_RIDGE_EMPIRICAL_V2"
    assert FEATURE_SCHEMA_VERSION == "NFL_SPREAD_CONTEXT_FEATURES_V2"
    assert CAN_EXECUTE is False
    assert AUTOMATIC_CERTIFICATION is False
    assert AUTOMATIC_PROMOTION is False
    assert PROBABILITY_PUBLISHABLE is False


def test_spread_v2_uses_strictly_prior_rows_and_raw_nflverse_event_ids():
    events = []
    teams = ("AAA", "BBB")
    day = 0
    # Seed enough prior meetings to satisfy the leakage-safe minimum.
    for season in (2021, 2022):
        for week in range(1, 6):
            events.append(_event(season, week, day, teams[week % 2], teams[(week + 1) % 2], 24 + week, 17))
            day += 7
    rows, meta = build_margin_rows(events)
    assert rows
    assert len(rows) == len(meta)
    for row in rows:
        assert not row.event_id.startswith("NFL:")
        assert datetime.fromisoformat(row.feature_as_of) < datetime.fromisoformat(row.event_start_time)
        assert "spread" not in " ".join(row.features).lower()


def test_target_result_does_not_enter_its_own_feature_vector():
    base = []
    day = 0
    for week in range(1, 6):
        base.append(_event(2024, week, day, "AAA" if week % 2 else "BBB", "BBB" if week % 2 else "AAA", 24, 17))
        day += 7
    target_a = _event(2024, 6, day, "AAA", "BBB", 10, 7)
    target_b = dict(target_a, home_score=60, away_score=3, home_process_margin=57.0, away_process_margin=-57.0)
    rows_a, _ = build_margin_rows(base + [target_a])
    rows_b, _ = build_margin_rows(base + [target_b])
    assert rows_a[-1].event_id == rows_b[-1].event_id
    assert rows_a[-1].features == rows_b[-1].features
    assert rows_a[-1].margin != rows_b[-1].margin
