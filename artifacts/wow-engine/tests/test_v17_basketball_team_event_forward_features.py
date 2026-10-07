from __future__ import annotations

from datetime import date
from types import SimpleNamespace

import pytest

from v17.basketball_team_event_forward_features import (
    BasketballForwardFeatureError,
    build_basketball_forward_features,
    hydrate_current_basketball_features,
)
from basketball_team_event_specialist import FEATURE_NAMES, FEATURE_SCHEMA_VERSION


def _game(gid, day, home, away, hs, aas):
    return SimpleNamespace(
        game_id=gid,
        game_date=date.fromisoformat(day),
        home_team_id=home,
        away_team_id=away,
        home_score=hs,
        away_score=aas,
        home_win=hs > aas,
    )


def _history():
    rows = []
    for index, day in enumerate(("2026-09-01","2026-09-03","2026-09-05","2026-09-07","2026-09-09","2026-09-11")):
        rows.append(_game(f"h{index}", day, "espn-1", f"opp-h-{index}", 100 + index, 90))
        rows.append(_game(f"a{index}", day, f"opp-a-{index}", "espn-2", 95, 98 + index))
    return rows


def test_forward_features_match_exact_fitted_schema_and_are_pregame_only():
    rows = _history()
    rows.append(_game("future", "2026-10-08", "espn-1", "espn-2", 200, 10))
    features, audit = build_basketball_forward_features(
        rows,
        target_date=date(2026, 10, 7),
        home_team_id="espn-1",
        away_team_id="espn-2",
    )
    assert tuple(features) == tuple(FEATURE_NAMES)
    assert audit["feature_schema_version"] == FEATURE_SCHEMA_VERSION
    assert audit["home_prior_games"] == 6
    assert audit["away_prior_games"] == 6
    assert audit["market_features_used"] is False
    assert audit["market_probability_used_as_model"] is False
    assert audit["can_execute"] is False


def test_forward_features_fail_closed_when_history_is_thin():
    with pytest.raises(BasketballForwardFeatureError) as exc:
        build_basketball_forward_features(
            _history()[:4],
            target_date=date(2026, 10, 7),
            home_team_id="espn-1",
            away_team_id="espn-2",
        )
    assert exc.value.code == "BASKETBALL_FORWARD_HISTORY_INSUFFICIENT"


def test_nba_hydration_uses_canonical_identity_and_never_publishes(monkeypatch):
    monkeypatch.setattr(
        "v17.basketball_team_event_forward_features.load_games",
        lambda _db, sport: _history(),
    )

    def resolver(**_kwargs):
        return {
            "event_id": "espn-401999999",
            "home_team_id": "espn-1",
            "away_team_id": "espn-2",
            "identity_provider": "SPORTSDATAVERSE_ESPN",
            "identity_resolution": "SPORTSDATAVERSE_SCHEDULE_EXACT_TEAM_DATE_MATCH",
            "can_execute": False,
        }

    out = hydrate_current_basketball_features(
        object(),
        sport="NBA",
        event_start_time="2026-10-07T23:00:00Z",
        home_team_alias="espn-1",
        away_team_alias="espn-2",
        nba_identity_resolver=resolver,
    )
    assert out["status"] == "PASS"
    assert out["official_event_id"] == "espn-401999999"
    assert out["feature_schema_version"] == FEATURE_SCHEMA_VERSION
    assert tuple(out["features"]) == tuple(FEATURE_NAMES)
    assert out["probability_publishable"] is False
    assert out["rank_eligible"] is False
    assert out["can_execute"] is False


def test_wnba_hydration_requires_exact_event_identity(monkeypatch):
    monkeypatch.setattr(
        "v17.basketball_team_event_forward_features.load_games",
        lambda _db, sport: _history(),
    )
    with pytest.raises(BasketballForwardFeatureError) as exc:
        hydrate_current_basketball_features(
            object(),
            sport="WNBA",
            event_start_time="2026-10-08T00:00:00Z",
            home_team_alias="espn-1",
            away_team_alias="espn-2",
        )
    assert exc.value.code == "WNBA_EVENT_ID_REQUIRED"
