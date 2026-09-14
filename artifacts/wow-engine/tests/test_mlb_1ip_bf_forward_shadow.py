from datetime import datetime, timezone

import mlb_1ip_live_acquisition as acquisition
from mlb_1ip_bf_forward_shadow import (
    EXPECTED_ARTIFACT_CHECKSUM,
    _actual_opener_bf,
    _binary_grade,
    _load_artifact,
)


class _Response:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self._payload


def test_frozen_artifact_is_research_only_and_checksum_locked():
    artifact = _load_artifact()
    assert artifact["artifact_checksum"] == EXPECTED_ARTIFACT_CHECKSUM
    assert artifact["historical_validation"]["historical_validation_passed"] is True
    assert artifact["recent_history_limit"] == 10
    assert artifact["probability_publishable"] is False
    assert artifact["rank_eligible"] is False
    assert artifact["can_execute"] is False


def test_recent_bf_hydrator_returns_exact_counts_and_caps_history(monkeypatch):
    monkeypatch.setattr(
        acquisition,
        "_schedule_games_for_pitcher",
        lambda player_id, event_start, http_get: [10, 9, 8, 7, 6, 5],
    )
    bfs = {10: 3, 9: 4, 8: 5, 7: 6, 6: 3, 5: 4}
    monkeypatch.setattr(
        acquisition,
        "_first_inning_pitch_counts",
        lambda game_pk, pitcher_id, http_get: (bfs[game_pk], [4] * bfs[game_pk]),
    )
    now = datetime(2026, 9, 14, 12, tzinfo=timezone.utc)
    out = acquisition.hydrate_mlb_1ip_bf_recent_history(
        pitcher_id=123,
        event_start_time="2026-09-14T20:00:00+00:00",
        now=now,
        http_get=lambda *args, **kwargs: None,
    )
    assert out["recent_bf_counts"] == {"3": 2, "4": 2, "5_PLUS": 2}
    assert out["pitcher_bf_distribution"]["sample_n"] == 6
    assert len(out["recent_bf_history"]) == 6
    assert out["history_limit"] == 10
    assert out["probability_publishable"] is False
    assert out["can_execute"] is False


def test_actual_opener_bf_counts_only_opening_pitcher_plate_appearances():
    payload = {
        "allPlays": [
            {"about": {"inning": 1, "halfInning": "top"}, "matchup": {"pitcher": {"id": 10}}, "playEvents": [{"isPitch": True}]},
            {"about": {"inning": 1, "halfInning": "top"}, "matchup": {"pitcher": {"id": 10}}, "playEvents": [{"isPitch": True}]},
            {"about": {"inning": 1, "halfInning": "top"}, "matchup": {"pitcher": {"id": 10}}, "playEvents": [{"isPitch": True}]},
            {"about": {"inning": 1, "halfInning": "top"}, "matchup": {"pitcher": {"id": 99}}, "playEvents": [{"isPitch": True}]},
            {"about": {"inning": 1, "halfInning": "bottom"}, "matchup": {"pitcher": {"id": 20}}, "playEvents": [{"isPitch": True}]},
            {"about": {"inning": 1, "halfInning": "bottom"}, "matchup": {"pitcher": {"id": 20}}, "playEvents": [{"isPitch": True}]},
            {"about": {"inning": 1, "halfInning": "bottom"}, "matchup": {"pitcher": {"id": 20}}, "playEvents": [{"isPitch": True}]},
        ]
    }
    status, bf, evidence = _actual_opener_bf(
        123,
        10,
        lambda *args, **kwargs: _Response(payload),
    )
    assert status == "GRADED"
    assert bf == 3
    assert evidence["first_pitcher_by_half"]["TOP"] == 10
    assert evidence["bf_by_half"]["TOP"] == 3


def test_actual_opener_bf_marks_probable_pitcher_change():
    payload = {
        "allPlays": [
            {"about": {"inning": 1, "halfInning": "top"}, "matchup": {"pitcher": {"id": 11}}, "playEvents": [{"isPitch": True}]},
            {"about": {"inning": 1, "halfInning": "top"}, "matchup": {"pitcher": {"id": 11}}, "playEvents": [{"isPitch": True}]},
            {"about": {"inning": 1, "halfInning": "top"}, "matchup": {"pitcher": {"id": 11}}, "playEvents": [{"isPitch": True}]},
        ]
    }
    status, bf, _ = _actual_opener_bf(
        123,
        10,
        lambda *args, **kwargs: _Response(payload),
    )
    assert status == "STARTER_CHANGED"
    assert bf is None


def test_binary_grade_is_finite_and_directionally_correct():
    brier_hit, log_hit = _binary_grade(0.8, True)
    brier_miss, log_miss = _binary_grade(0.8, False)
    assert brier_hit < brier_miss
    assert log_hit < log_miss
    assert brier_hit >= 0
    assert log_hit >= 0
