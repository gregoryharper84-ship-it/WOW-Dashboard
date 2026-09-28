from __future__ import annotations

from datetime import datetime, timedelta, timezone

from v17.nfl_event_context_challenger import (
    CAN_EXECUTE,
    FEATURE_ORDER,
    FEATURE_SCHEMA_VERSION,
    MODEL_FAMILY,
    build_rows,
)
from v17.team_state_scoped_maintenance import supported_scopes


def _event(
    *,
    event_id: str,
    start: datetime,
    season: int,
    week: int,
    a_home: bool,
    a_wins: bool,
    a_qb: str,
    b_qb: str,
) -> dict:
    home = "A" if a_home else "B"
    away = "B" if a_home else "A"
    a_score, b_score = ((24.0, 17.0) if a_wins else (17.0, 24.0))
    home_score = a_score if a_home else b_score
    away_score = b_score if a_home else a_score
    home_qb = a_qb if a_home else b_qb
    away_qb = b_qb if a_home else a_qb
    return {
        "event_id": event_id,
        "event_start_time": start.isoformat(),
        "season": season,
        "week": week,
        "home_team": home,
        "away_team": away,
        "home_score": home_score,
        "away_score": away_score,
        "home_process_margin": 0.20 if home_score > away_score else -0.20,
        "away_process_margin": -0.20 if home_score > away_score else 0.20,
        "home_turnovers": 1.0 if home_score > away_score else 2.0,
        "away_turnovers": 2.0 if home_score > away_score else 1.0,
        "home_sacks_allowed": 1.0 if home_score > away_score else 3.0,
        "away_sacks_allowed": 3.0 if home_score > away_score else 1.0,
        "home_special_teams_epa": 0.10 if home_score > away_score else -0.10,
        "away_special_teams_epa": -0.10 if home_score > away_score else 0.10,
        "home_history_lineup_ids": [home_qb],
        "away_history_lineup_ids": [away_qb],
        "source_manifest": {"source": "SYNTHETIC_PRIOR_ONLY", "event_id": event_id},
    }


def _week3_fixture(*, target_qb_a: str = "QB-A-TARGET", target_qb_b: str = "QB-B-TARGET") -> list[dict]:
    events: list[dict] = []
    start_2025 = datetime(2025, 10, 1, 18, tzinfo=timezone.utc)
    for index in range(8):
        events.append(
            _event(
                event_id=f"2025-{index}",
                start=start_2025 + timedelta(days=7 * index),
                season=2025,
                week=10 + index,
                a_home=(index % 2 == 0),
                a_wins=True,
                a_qb="QB-A-OLD",
                b_qb="QB-B-OLD",
            )
        )

    start_2026 = datetime(2026, 9, 6, 18, tzinfo=timezone.utc)
    for index in range(2):
        events.append(
            _event(
                event_id=f"2026-{index + 1}",
                start=start_2026 + timedelta(days=7 * index),
                season=2026,
                week=index + 1,
                a_home=(index % 2 == 0),
                a_wins=False,
                a_qb="QB-A-NEW",
                b_qb="QB-B-NEW",
            )
        )

    events.append(
        _event(
            event_id="2026-W3-TARGET",
            start=start_2026 + timedelta(days=14),
            season=2026,
            week=3,
            a_home=True,
            a_wins=False,
            a_qb=target_qb_a,
            b_qb=target_qb_b,
        )
    )
    return events


def _target_row(events: list[dict]):
    rows, metadata = build_rows(events)
    row = next(row for row in rows if row.event_id == "2026-W3-TARGET")
    meta = metadata[rows.index(row)]
    return row, meta


def test_week3_current_season_state_is_separate_from_previous_season_state():
    row, _ = _target_row(_week3_fixture())
    features = row.features

    # Two current-season games out of the 8-game reference depth is 25%.
    # V1's blended recent-8 window at Week 3 was historically 75% prior season;
    # this challenger does not encode that blend as current form.
    assert features["home_current_season_share"] == 0.25
    assert features["away_current_season_share"] == 0.25

    # A dominated the previous season but lost both 2026 games. The model gets
    # those as distinct fitted signals instead of one blended recent-8 average.
    assert features["previous_season_win_rate_edge"] == 1.0
    assert features["season_win_rate_edge"] == -1.0
    assert features["early_week_1_4"] == 1.0


def test_target_game_qb_ids_cannot_change_target_features():
    first, _ = _target_row(
        _week3_fixture(target_qb_a="TARGET-A-ONE", target_qb_b="TARGET-B-ONE")
    )
    second, _ = _target_row(
        _week3_fixture(target_qb_a="TARGET-A-TWO", target_qb_b="TARGET-B-TWO")
    )
    assert first.features == second.features
    assert first.source_manifest_sha256 == second.source_manifest_sha256


def test_feature_snapshot_is_strictly_pregame_and_manifest_is_non_market():
    row, metadata = _target_row(_week3_fixture())
    assert datetime.fromisoformat(row.feature_as_of) < datetime.fromisoformat(row.event_start_time)
    manifest = metadata["source_manifest"]
    assert manifest["market_features_used"] is False
    assert manifest["manual_probability_adjustments"] is False
    assert manifest["target_game_participant_ids_used"] is False
    assert manifest["v1_recent8_blend_reused"] is False


def test_challenger_has_no_market_or_underdog_feature_and_no_execution_authority():
    lowered = " ".join(FEATURE_ORDER).lower()
    assert "odds" not in lowered
    assert "market" not in lowered
    assert "underdog" not in lowered
    assert "spread" not in lowered
    assert CAN_EXECUTE is False
    assert MODEL_FAMILY == "NFL_EVENT_CONTEXT_LOGIT_V2"
    assert FEATURE_SCHEMA_VERSION == "NFL_EVENT_CONTEXT_FEATURES_V2"


def test_scoped_maintenance_exposes_candidate_without_replacing_nfl_scope():
    scopes = supported_scopes()
    assert "NFL" in scopes
    assert "NFL_EVENT_V2" in scopes
