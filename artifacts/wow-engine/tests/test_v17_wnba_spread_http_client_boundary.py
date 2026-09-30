from __future__ import annotations

from datetime import datetime, timedelta, timezone

import httpx

import v17.spread_forward_auto_canary as canary
import v17.spread_forward_shadow_leagues as shadow
from v17.spread_margin_challenger import SpreadChallengerUnavailable


NOW = datetime(2026, 9, 29, 6, 0, tzinfo=timezone.utc)
FUTURE = NOW + timedelta(days=1)


def _stats_schedule():
    return {
        "leagueSchedule": {
            "gameDates": [{
                "gameDate": FUTURE.date().isoformat(),
                "games": [{
                    "gameId": "1022600180",
                    "gameDateTimeUTC": FUTURE.isoformat(),
                    "gameStatus": 1,
                    "homeTeam": {"teamTricode": "MIN", "teamCity": "Minnesota", "teamName": "Lynx"},
                    "awayTeam": {"teamTricode": "PHX", "teamCity": "Phoenix", "teamName": "Mercury"},
                }],
            }],
        },
    }


def test_stats_discovery_uses_httpx_compatible_fetcher(monkeypatch):
    seen = {}

    def fake_scoreboard(requested_date, *, http_get):
        seen["date"] = requested_date
        seen["http_get"] = http_get
        return _stats_schedule()

    import v17.wnba_prop_evidence_control_plane as control
    monkeypatch.setattr(control, "_scoreboard_schedule_for_date", fake_scoreboard)

    def stats_get(*_args, **_kwargs):
        raise AssertionError("network should be intercepted by fake_scoreboard")

    result = canary.discover_future_wnba_stats_event(
        fetcher=stats_get,
        now=NOW,
        horizon_days=2,
    )
    assert seen["http_get"] is stats_get
    assert result is not None
    assert result["identity_provider"] == "WNBA_STATS_SCOREBOARD_V3"
    assert result["raw_event_id"] == "1022600180"
    assert result["home_team_id"] == "espn-8"
    assert result["away_team_id"] == "espn-11"
    assert result["can_execute"] is False


def test_auto_canary_does_not_reuse_requests_fetcher_for_stats_fallback(monkeypatch):
    espn_fetcher = object()
    stats_fetcher = object()
    seen = {}

    def espn(_sport, *, fetcher, **_kwargs):
        assert fetcher is espn_fetcher
        raise SpreadChallengerUnavailable(
            "WNBA_SPREAD_CANARY_IDENTITY_SOURCE_UNAVAILABLE",
            "ESPN blocked",
        )

    def stats(*, fetcher, **_kwargs):
        seen["stats_fetcher"] = fetcher
        return {
            "sport": "WNBA",
            "raw_event_id": "1022600180",
            "event_start_time": FUTURE.isoformat(),
            "home_team": "Minnesota Lynx",
            "away_team": "Phoenix Mercury",
            "home_team_id": "espn-8",
            "away_team_id": "espn-11",
            "identity_provider": "WNBA_STATS_SCOREBOARD_V3",
            "identity_acquisition_location": "BACKEND_RUNTIME",
            "can_execute": False,
        }

    monkeypatch.setattr(canary, "discover_future_espn_event", espn)
    monkeypatch.setattr(canary, "discover_future_wnba_stats_event", stats)
    monkeypatch.setattr(
        canary,
        "run_wnba_forward_shadow",
        lambda _db, **_kwargs: {
            "status": "EXPERIMENT_CREATED",
            "code": "WNBA_SPREAD_FORWARD_SHADOW_COMPLETE",
            "sport": "WNBA",
            "p_cover": 0.5,
            "p_push": 0.0,
            "p_not_cover": 0.5,
        },
    )

    result = canary.run_wnba_spread_auto_canary(
        object(),
        fetcher=espn_fetcher,
        stats_fetcher=stats_fetcher,
    )
    assert seen["stats_fetcher"] is stats_fetcher
    assert result["identity_provider"] == "WNBA_STATS_SCOREBOARD_V3"
    assert result["probability_publishable"] is False
    assert result["automatic_certification"] is False
    assert result["automatic_promotion"] is False
    assert result["can_execute"] is False


def test_forward_shadow_verifies_wnba_identity_with_httpx(monkeypatch):
    seen = {}

    def resolver(**kwargs):
        seen.update(kwargs)
        return {
            "event_id": kwargs["event_id"],
            "event_start_time": kwargs["event_start_time"],
            "home_team_id": kwargs["home_team_id"],
            "away_team_id": kwargs["away_team_id"],
            "identity_provider": "WNBA_STATS_SCOREBOARD_V3",
            "market_features_used": False,
            "can_execute": False,
        }

    class Artifact:
        feature_names = tuple(sorted([
            "away_back_to_back",
            "away_point_diff_prior",
            "away_rest_days_capped",
            "away_win_rate_prior",
            "home_back_to_back",
            "home_point_diff_prior",
            "home_rest_days_capped",
            "home_win_rate_prior",
        ]))
        model_family = "fixture"
        feature_schema_version = "fixture"
        training_dataset_hash = "fixture"
        train_rows = 400
        calibration_rows = 50
        test_rows = 50

    monkeypatch.setattr(shadow, "resolve_wnba_current_event_identity", resolver)
    monkeypatch.setattr(shadow, "load_basketball_games", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(
        shadow,
        "build_wnba_forward_features",
        lambda *_args, **_kwargs: ({name: 0.0 for name in Artifact.feature_names}, {"can_execute": False}),
    )
    monkeypatch.setattr(
        shadow,
        "_fit_forward_artifact",
        lambda *_args, **_kwargs: (Artifact(), NOW),
    )
    monkeypatch.setattr(
        shadow,
        "score_home_spread",
        lambda *_args, **_kwargs: {
            "predicted_home_margin_center": 0.0,
            "p_cover": 0.5,
            "p_push": 0.0,
            "p_not_cover": 0.5,
            "p_cover_given_no_push": 0.5,
            "research_lower_bound_cover": 0.45,
            "distribution_sample_n": 500,
        },
    )

    result = shadow.run_wnba_forward_shadow(
        object(),
        event_id="wnba-stats-1022600180",
        event_start_time=FUTURE.isoformat(),
        home_team_id="espn-8",
        away_team_id="espn-11",
        home_spread=0.0,
    )
    assert seen["fetcher"] is httpx.get
    assert result["status"] == "EXPERIMENT_CREATED"
    assert result["probability_publishable"] is False
    assert result["can_execute"] is False
