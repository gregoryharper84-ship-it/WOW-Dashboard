from datetime import date

from nfl_event_features_p2 import MAX_REST_DAYS, _prior_metrics


def test_offseason_rest_is_capped_without_changing_temporal_provenance():
    records = [{
        "game_id": "prior",
        "season": 2025,
        "gameday": date(2026, 1, 4),
        "offensive_plays": 60,
        "offensive_epa_sum": 3.0,
        "defensive_epa_mean": -0.02,
        "success_plays": 30,
        "turnovers": 1,
        "sacks_allowed": 2,
        "special_teams_epa_sum": 0.1,
        "points_for": 24,
        "points_against": 20,
        "win_value": 1.0,
        "schedule_content_sha256": "a" * 64,
        "pbp_content_sha256": "b" * 64,
    }]
    metrics = _prior_metrics(records, target_date=date(2026, 9, 10), season=2026)
    assert metrics["rest_days"] == float(MAX_REST_DAYS)
    assert metrics["max_prior_gameday"] == "2026-01-04"
