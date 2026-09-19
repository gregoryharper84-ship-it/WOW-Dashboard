from __future__ import annotations

import copy

import pytest

import nhl_skater_sog_feature_hydration as feat
import nhl_skater_sog_ingestion as ing


def _team(team_id: int, abbrev: str) -> dict:
    return {"id": team_id, "abbrev": abbrev}


def _skater(player_id: int, name: str, position: str, shots: int, toi: str = "18:00") -> dict:
    return {"playerId": player_id, "name": {"default": name}, "position": position, "sog": shots, "toi": toi}


def _game(
    game_id: str,
    start: str,
    *,
    home_id: int = 10,
    home_abbrev: str = "TOR",
    away_id: int = 20,
    away_abbrev: str = "NYR",
    season: str = "20252026",
    home_forwards=None,
    home_defense=None,
    away_forwards=None,
    away_defense=None,
    game_state: str = "OFF",
) -> dict:
    return {
        "id": game_id,
        "season": season,
        "gameState": game_state,
        "startTimeUTC": start,
        "homeTeam": _team(home_id, home_abbrev),
        "awayTeam": _team(away_id, away_abbrev),
        "playerByGameStats": {
            "homeTeam": {"forwards": home_forwards or [], "defense": home_defense or []},
            "awayTeam": {"forwards": away_forwards or [], "defense": away_defense or []},
        },
    }


def _ingest(raw, scratches=None):
    return ing.ingest_settled_game_skater_sog(raw, scratches_raw=scratches, source_uri="u", retrieved_at="t").records


def _veteran_history(player_id=100, opponent_id=20, own_team=10, n=6, start_dates=None, shots=4):
    dates = start_dates or [
        "2025-10-01T00:00:00Z", "2025-10-03T00:00:00Z", "2025-10-05T00:00:00Z",
        "2025-10-07T00:00:00Z", "2025-10-09T00:00:00Z", "2025-10-11T00:00:00Z",
    ]
    history = []
    for i, start in enumerate(dates[:n]):
        raw = _game(
            f"G{i}", start, home_id=own_team, away_id=opponent_id,
            home_forwards=[_skater(player_id, "Vet Player", "C", shots)],
            away_forwards=[_skater(200 + i, "Opp Skater", "LW", 2)],
        )
        history.extend(_ingest(raw))
    return history


def test_normal_veteran_skater_full_windows():
    history = _veteran_history()
    snap = feat.hydrate_pregame_snapshot(
        event_id="TARGET", player_id="100", team_id="10", opponent_team_id="20", is_home=True,
        target_provider_season_id="20252026", event_start="2025-10-13T00:00:00Z", as_of="2025-10-12T00:00:00Z",
        history=history,
    )
    assert snap.feature_values["rolling_sog_per_game_5"] == 4.0
    assert snap.feature_sample_counts["rolling_sog_per_game_5"] == 5
    assert snap.freshness_status == feat.FRESHNESS_FRESH
    assert not snap.missing_features


def test_dressed_player_with_prior_zero_sog_games_is_included_not_excluded():
    history = _veteran_history(shots=0)
    snap = feat.hydrate_pregame_snapshot(
        event_id="TARGET", player_id="100", team_id="10", opponent_team_id="20", is_home=True,
        target_provider_season_id="20252026", event_start="2025-10-13T00:00:00Z", as_of="2025-10-12T00:00:00Z",
        history=history,
    )
    assert snap.feature_values["rolling_sog_per_game_5"] == 0.0
    assert snap.feature_sample_counts["rolling_sog_per_game_5"] == 5  # zeros still count as qualifying games


def test_rookie_small_sample_reports_reduced_sample_count_not_fabricated_average():
    history = _veteran_history(n=2, start_dates=["2025-10-01T00:00:00Z", "2025-10-05T00:00:00Z"])
    snap = feat.hydrate_pregame_snapshot(
        event_id="TARGET", player_id="100", team_id="10", opponent_team_id="20", is_home=True,
        target_provider_season_id="20252026", event_start="2025-10-13T00:00:00Z", as_of="2025-10-12T00:00:00Z",
        history=history,
    )
    assert snap.feature_sample_counts["rolling_sog_per_game_5"] == 2
    assert snap.feature_values["rolling_sog_per_game_5"] == 4.0
    assert snap.freshness_status == feat.FRESHNESS_DEGRADED


def test_rookie_with_zero_history_marks_features_missing_not_league_average():
    history: list = []
    snap = feat.hydrate_pregame_snapshot(
        event_id="TARGET", player_id="999", team_id="10", opponent_team_id="20", is_home=True,
        target_provider_season_id="20252026", event_start="2025-10-13T00:00:00Z", as_of="2025-10-12T00:00:00Z",
        history=history,
    )
    assert snap.feature_values["rolling_sog_per_game_5"] is None
    assert snap.feature_sample_counts["rolling_sog_per_game_5"] == 0
    assert "rolling_sog_per_game_5" in snap.missing_features
    assert snap.missing_feature_reasons["rolling_sog_per_game_5"] == "NO_QUALIFYING_GAMES_IN_WINDOW"
    assert snap.freshness_status == feat.FRESHNESS_STALE


def test_traded_player_history_across_teams_still_qualifies_by_player_id():
    early = _ingest(_game("G0", "2025-10-01T00:00:00Z", home_id=10, home_forwards=[_skater(100, "P", "C", 3)]))
    later = _ingest(_game("G1", "2025-10-05T00:00:00Z", home_id=30, away_id=20, home_forwards=[_skater(100, "P", "C", 5)]))
    history = list(early) + list(later)
    snap = feat.hydrate_pregame_snapshot(
        event_id="TARGET", player_id="100", team_id="30", opponent_team_id="20", is_home=True,
        target_provider_season_id="20252026", event_start="2025-10-10T00:00:00Z", as_of="2025-10-09T00:00:00Z",
        history=history,
    )
    assert snap.feature_sample_counts["rolling_sog_per_game_5"] == 2
    assert snap.feature_values["rolling_sog_per_game_5"] == 4.0  # mean of both teams' games


def test_recent_toi_change_delta_reflects_role_change_without_a_subjective_flag():
    # 10 prior games: first 8 at a low TOI role, most recent 2 at a bumped
    # TOI -- window-5 (mixed) should read higher than window-20 (mostly low).
    dates = [f"2025-10-{day:02d}T00:00:00Z" for day in range(1, 21, 2)]
    history = []
    for i, start in enumerate(dates):
        toi = "12:00" if i < 8 else "22:00"
        raw = _game("G%d" % i, start, home_forwards=[_skater(100, "P", "C", 4, toi=toi)])
        history.extend(_ingest(raw))
    snap = feat.hydrate_pregame_snapshot(
        event_id="TARGET", player_id="100", team_id="10", opponent_team_id="20", is_home=True,
        target_provider_season_id="20252026", event_start="2025-10-21T00:00:00Z", as_of="2025-10-20T00:00:00Z",
        history=history,
    )
    assert snap.feature_values["recent_vs_long_toi_delta_5_20"] > 0
    assert not any(k.upper().startswith("ROLE_UPGRADE") for k in snap.feature_values)


def test_opponent_short_window_suppression_derived_from_prior_games_only():
    history = _veteran_history(opponent_id=20)
    snap = feat.hydrate_pregame_snapshot(
        event_id="TARGET", player_id="100", team_id="10", opponent_team_id="20", is_home=True,
        target_provider_season_id="20252026", event_start="2025-10-13T00:00:00Z", as_of="2025-10-12T00:00:00Z",
        history=history,
    )
    # opponent_team_id=20 is the away team in each prior game; "shots
    # allowed" by the opponent is the attacking (home) team's total shots.
    assert snap.feature_values["opponent_sog_allowed_per_game_5"] == 4.0
    assert snap.feature_sample_counts["opponent_qualifying_games_available"] == 6


def test_home_away_feature():
    history = _veteran_history()
    home_snap = feat.hydrate_pregame_snapshot(
        event_id="T1", player_id="100", team_id="10", opponent_team_id="20", is_home=True,
        target_provider_season_id="20252026", event_start="2025-10-13T00:00:00Z", as_of="2025-10-12T00:00:00Z",
        history=history,
    )
    away_snap = feat.hydrate_pregame_snapshot(
        event_id="T2", player_id="100", team_id="10", opponent_team_id="20", is_home=False,
        target_provider_season_id="20252026", event_start="2025-10-13T00:00:00Z", as_of="2025-10-12T00:00:00Z",
        history=history,
    )
    assert home_snap.feature_values["is_home"] == 1.0
    assert away_snap.feature_values["is_home"] == 0.0


def test_back_to_back_flag_from_rest_days():
    history = _veteran_history()  # most recent prior game 2025-10-11
    b2b_snap = feat.hydrate_pregame_snapshot(
        event_id="T1", player_id="100", team_id="10", opponent_team_id="20", is_home=True,
        target_provider_season_id="20252026", event_start="2025-10-12T00:00:00Z", as_of="2025-10-11T12:00:00Z",
        history=history,
    )
    rested_snap = feat.hydrate_pregame_snapshot(
        event_id="T2", player_id="100", team_id="10", opponent_team_id="20", is_home=True,
        target_provider_season_id="20252026", event_start="2025-10-15T00:00:00Z", as_of="2025-10-14T00:00:00Z",
        history=history,
    )
    assert b2b_snap.feature_values["back_to_back"] == 1.0
    assert rested_snap.feature_values["back_to_back"] == 0.0
    assert rested_snap.feature_values["rest_days"] > b2b_snap.feature_values["rest_days"]


def test_postponed_rescheduled_game_uses_authoritative_new_start():
    history = _veteran_history()
    original_start = "2025-10-13T00:00:00Z"
    rescheduled_start = "2025-10-20T00:00:00Z"
    original_snap = feat.hydrate_pregame_snapshot(
        event_id="TARGET", player_id="100", team_id="10", opponent_team_id="20", is_home=True,
        target_provider_season_id="20252026", event_start=original_start, as_of="2025-10-12T00:00:00Z",
        history=history,
    )
    rescheduled_snap = feat.hydrate_pregame_snapshot(
        event_id="TARGET", player_id="100", team_id="10", opponent_team_id="20", is_home=True,
        target_provider_season_id="20252026", event_start=rescheduled_start, as_of="2025-10-19T00:00:00Z",
        history=history,
    )
    # Same underlying history, but the rescheduled evaluation is anchored to
    # the new authoritative start/as_of and produces its own valid snapshot.
    assert original_snap.event_start == original_start
    assert rescheduled_snap.event_start == rescheduled_start
    assert rescheduled_snap.feature_sample_counts["rolling_sog_per_game_5"] == 5


def test_as_of_before_latest_prior_game_settlement_excludes_it():
    history = _veteran_history()  # latest game start 2025-10-11
    snap = feat.hydrate_pregame_snapshot(
        event_id="TARGET", player_id="100", team_id="10", opponent_team_id="20", is_home=True,
        target_provider_season_id="20252026", event_start="2025-10-13T00:00:00Z", as_of="2025-10-10T00:00:00Z",
        history=history,
    )
    assert snap.feature_sample_counts["career_qualifying_games_available"] == 5  # excludes the 10-11 game (6 total)


def test_as_of_after_settlement_includes_it():
    history = _veteran_history()
    snap = feat.hydrate_pregame_snapshot(
        event_id="TARGET", player_id="100", team_id="10", opponent_team_id="20", is_home=True,
        target_provider_season_id="20252026", event_start="2025-10-13T00:00:00Z", as_of="2025-10-12T00:00:00Z",
        history=history,
    )
    assert snap.feature_sample_counts["career_qualifying_games_available"] == 6  # includes the 10-11 game


def test_attempted_current_game_leakage_is_rejected():
    history = _veteran_history()
    leaking_raw = _game(
        "TARGET", "2025-10-01T00:00:00Z",  # same event_id as target, forged early timestamp
        home_forwards=[_skater(100, "P", "C", 99)],
    )
    leaking_record = _ingest(leaking_raw)
    poisoned_history = history + list(leaking_record)
    snap = feat.hydrate_pregame_snapshot(
        event_id="TARGET", player_id="100", team_id="10", opponent_team_id="20", is_home=True,
        target_provider_season_id="20252026", event_start="2025-10-13T00:00:00Z", as_of="2025-10-12T00:00:00Z",
        history=poisoned_history,
    )
    assert "TARGET" not in snap.evidence_ids
    assert snap.feature_sample_counts["career_qualifying_games_available"] == 6  # TARGET's forged record excluded


def test_future_game_leakage_is_rejected():
    history = _veteran_history()
    future_raw = _game("FUTURE", "2025-12-01T00:00:00Z", home_forwards=[_skater(100, "P", "C", 7)])
    poisoned_history = history + list(_ingest(future_raw))
    snap = feat.hydrate_pregame_snapshot(
        event_id="TARGET", player_id="100", team_id="10", opponent_team_id="20", is_home=True,
        target_provider_season_id="20252026", event_start="2025-10-13T00:00:00Z", as_of="2025-10-12T00:00:00Z",
        history=poisoned_history,
    )
    assert "FUTURE" not in snap.evidence_ids


def test_as_of_at_or_after_puck_drop_rejected():
    history = _veteran_history()
    with pytest.raises(feat.NHLSogFeatureError) as exc:
        feat.hydrate_pregame_snapshot(
            event_id="TARGET", player_id="100", team_id="10", opponent_team_id="20", is_home=True,
            target_provider_season_id="20252026", event_start="2025-10-13T00:00:00Z", as_of="2025-10-13T00:00:00Z",
            history=history,
        )
    assert exc.value.code == "EVENT_ALREADY_STARTED"


def test_missing_historical_feature_reports_reason_metadata():
    history = _veteran_history(n=0, start_dates=[])
    snap = feat.hydrate_pregame_snapshot(
        event_id="TARGET", player_id="100", team_id="10", opponent_team_id="20", is_home=True,
        target_provider_season_id="20252026", event_start="2025-10-13T00:00:00Z", as_of="2025-10-12T00:00:00Z",
        history=history,
    )
    assert "rest_days" in snap.missing_features
    assert snap.missing_feature_reasons["rest_days"] == "NO_PRIOR_TEAM_GAME_AVAILABLE"


def test_deterministic_replay_same_inputs_same_snapshot():
    history = _veteran_history()
    kwargs = dict(
        event_id="TARGET", player_id="100", team_id="10", opponent_team_id="20", is_home=True,
        target_provider_season_id="20252026", event_start="2025-10-13T00:00:00Z", as_of="2025-10-12T00:00:00Z",
    )
    snap_1 = feat.hydrate_pregame_snapshot(history=history, **kwargs)
    snap_2 = feat.hydrate_pregame_snapshot(history=copy.deepcopy(history), **kwargs)
    assert snap_1.to_dict() == snap_2.to_dict()


def test_snapshot_hash_stability_and_sensitivity():
    history = _veteran_history()
    kwargs = dict(
        event_id="TARGET", player_id="100", team_id="10", opponent_team_id="20", is_home=True,
        target_provider_season_id="20252026", event_start="2025-10-13T00:00:00Z", as_of="2025-10-12T00:00:00Z",
    )
    snap_1 = feat.hydrate_pregame_snapshot(history=history, **kwargs)
    snap_2 = feat.hydrate_pregame_snapshot(history=history, **kwargs)
    assert snap_1.snapshot_hash == snap_2.snapshot_hash

    changed_kwargs = dict(kwargs, as_of="2025-10-11T00:00:00Z")
    snap_3 = feat.hydrate_pregame_snapshot(history=history, **changed_kwargs)
    assert snap_3.snapshot_hash != snap_1.snapshot_hash


def test_source_and_provenance_preserved_in_snapshot():
    history = _veteran_history()
    snap = feat.hydrate_pregame_snapshot(
        event_id="TARGET", player_id="100", team_id="10", opponent_team_id="20", is_home=True,
        target_provider_season_id="20252026", event_start="2025-10-13T00:00:00Z", as_of="2025-10-12T00:00:00Z",
        history=history,
    )
    assert snap.source_ids == (ing.SOURCE_ID,)
    assert len(snap.evidence_ids) > 0
    assert snap.can_execute is False
    assert snap.research_evidence_only is True


def test_no_market_derived_features_present():
    history = _veteran_history()
    snap = feat.hydrate_pregame_snapshot(
        event_id="TARGET", player_id="100", team_id="10", opponent_team_id="20", is_home=True,
        target_provider_season_id="20252026", event_start="2025-10-13T00:00:00Z", as_of="2025-10-12T00:00:00Z",
        history=history,
    )
    forbidden_substrings = ("market", "odds", "line", "prizepicks", "implied", "sportsbook")
    for key in snap.feature_values:
        lowered = key.lower()
        assert not any(term in lowered for term in forbidden_substrings), key
