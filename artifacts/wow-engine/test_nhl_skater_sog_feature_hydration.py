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
    settled_at: str | None = None,
    roster_fully_resolved: dict | None = None,
    team_sog_totals: dict | None = None,
) -> dict:
    payload = {
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
        # Default every fixture to full-roster-asserted on both sides so
        # opponent-suppression math is trustable without needing an official
        # total in every single test; individual tests override this to
        # exercise the reconciliation-exclusion paths explicitly.
        "rosterFullyResolved": roster_fully_resolved if roster_fully_resolved is not None else {"home": True, "away": True},
    }
    if settled_at is not None:
        payload["settledAtUTC"] = settled_at
    if team_sog_totals is not None:
        payload["teamSogTotals"] = team_sog_totals
    return payload


def _ingest(raw, scratches=None, retrieved_at=None):
    # Default: settle/retrieve the box score the same day the game started,
    # so a fixture's availability never accidentally drifts into a different
    # month than its own game dates. Tests that specifically exercise a
    # delayed settlement pass an explicit retrieved_at.
    retrieved_at = retrieved_at or str(raw.get("startTimeUTC") or "2025-01-01T00:00:00Z")
    return ing.ingest_settled_game_skater_sog(raw, scratches_raw=scratches, source_uri="u", retrieved_at=retrieved_at).records


def _veteran_history(player_id=100, opponent_id=20, own_team=10, n=6, start_dates=None, shots=4, game_id_prefix="G", **kwargs):
    dates = start_dates or [
        "2025-10-01T00:00:00Z", "2025-10-03T00:00:00Z", "2025-10-05T00:00:00Z",
        "2025-10-07T00:00:00Z", "2025-10-09T00:00:00Z", "2025-10-11T00:00:00Z",
    ]
    history = []
    for i, start in enumerate(dates[:n]):
        raw = _game(
            f"{game_id_prefix}{i}", start, home_id=own_team, away_id=opponent_id,
            home_forwards=[_skater(player_id, "Vet Player", "C", shots)],
            away_forwards=[_skater(200 + i, "Opp Skater", "LW", 2)],
            **kwargs,
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
    assert snap.sample_sufficiency_status == feat.SAMPLE_SUFFICIENT
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
    assert snap.feature_sample_counts["rolling_sog_per_game_5"] == 5


def test_rookie_small_sample_reports_reduced_sample_count_not_fabricated_average():
    history = _veteran_history(n=2, start_dates=["2025-10-01T00:00:00Z", "2025-10-05T00:00:00Z"])
    snap = feat.hydrate_pregame_snapshot(
        event_id="TARGET", player_id="100", team_id="10", opponent_team_id="20", is_home=True,
        target_provider_season_id="20252026", event_start="2025-10-13T00:00:00Z", as_of="2025-10-12T00:00:00Z",
        history=history,
    )
    assert snap.feature_sample_counts["rolling_sog_per_game_5"] == 2
    assert snap.feature_values["rolling_sog_per_game_5"] == 4.0
    assert snap.sample_sufficiency_status == feat.SAMPLE_PARTIAL


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
    assert snap.sample_sufficiency_status == feat.SAMPLE_INSUFFICIENT
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
    assert snap.feature_values["rolling_sog_per_game_5"] == 4.0


def test_recent_toi_change_delta_reflects_role_change_without_a_subjective_flag():
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


def test_opponent_short_window_suppression_derived_from_trusted_prior_games_only():
    history = _veteran_history(opponent_id=20)
    snap = feat.hydrate_pregame_snapshot(
        event_id="TARGET", player_id="100", team_id="10", opponent_team_id="20", is_home=True,
        target_provider_season_id="20252026", event_start="2025-10-13T00:00:00Z", as_of="2025-10-12T00:00:00Z",
        history=history,
    )
    assert snap.feature_values["opponent_sog_allowed_per_game_5"] == 4.0
    assert snap.feature_sample_counts["opponent_qualifying_games_available"] == 6


def test_incomplete_skater_coverage_excludes_opponent_suppression_observation():
    # No rosterFullyResolved and no teamSogTotals -> INCOMPLETE_SKATER_COVERAGE.
    history = _veteran_history(opponent_id=20, roster_fully_resolved={"home": False, "away": False})
    snap = feat.hydrate_pregame_snapshot(
        event_id="TARGET", player_id="100", team_id="10", opponent_team_id="20", is_home=True,
        target_provider_season_id="20252026", event_start="2025-10-13T00:00:00Z", as_of="2025-10-12T00:00:00Z",
        history=history,
    )
    assert snap.feature_values["opponent_sog_allowed_per_game_5"] is None
    assert snap.feature_sample_counts["opponent_qualifying_games_available"] == 0
    assert "opponent_sog_allowed_per_game_5" in snap.missing_features
    # The player's own rolling stats are unaffected by opponent-side coverage.
    assert snap.feature_values["rolling_sog_per_game_5"] == 4.0


def test_official_vs_summed_mismatch_excludes_that_team_game_from_suppression():
    # Opponent-suppression evidence for a defending team X is drawn from the
    # ATTACKING team's reconciled shot total in that game. Here team 10
    # (home, attacking against opponent 20) sums to 4 but claims an official
    # total of 99 -- a genuine mismatch -- so team 20's suppression evidence
    # for this game must be excluded entirely, not approximated from 4.
    raw = _game(
        "G0", "2025-10-01T00:00:00Z", home_id=10, away_id=20,
        home_forwards=[_skater(100, "P", "C", 4)],
        away_forwards=[_skater(200, "Opp", "LW", 2)],
        team_sog_totals={"home": 99, "away": 2},
    )
    history = list(_ingest(raw))
    snap = feat.hydrate_pregame_snapshot(
        event_id="TARGET", player_id="300", team_id="30", opponent_team_id="20", is_home=False,
        target_provider_season_id="20252026", event_start="2025-10-05T00:00:00Z", as_of="2025-10-04T00:00:00Z",
        history=history,
    )
    assert snap.feature_sample_counts["opponent_qualifying_games_available"] == 0
    assert snap.feature_values["opponent_sog_allowed_per_game_5"] is None


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


def test_back_to_back_flag_from_team_schedule_not_player_row():
    history = _veteran_history()  # team 10's most recent prior game start 2025-10-11
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


def test_call_up_whose_team_played_yesterday_gets_correct_team_rest_and_b2b():
    # Team 10 played a game with none of its usual skaters resolved for the
    # call-up's individual history -- the call-up (player 555) has ZERO
    # individual prior rows, but the team schedule still correctly reports
    # a 1-day rest / back-to-back for their next game.
    team_game = _ingest(_game(
        "G0", "2025-10-10T00:00:00Z", home_id=10, away_id=20,
        home_forwards=[_skater(999, "Regular", "C", 3)],
        away_forwards=[_skater(200, "Opp", "LW", 2)],
    ))
    snap = feat.hydrate_pregame_snapshot(
        event_id="TARGET", player_id="555", team_id="10", opponent_team_id="30", is_home=True,
        target_provider_season_id="20252026", event_start="2025-10-11T00:00:00Z", as_of="2025-10-10T12:00:00Z",
        history=list(team_game),
    )
    assert snap.feature_values["back_to_back"] == 1.0
    assert snap.feature_values["rest_days"] == 1.0
    # The call-up's own SOG history is genuinely empty -- not fabricated.
    assert snap.feature_sample_counts["career_qualifying_games_available"] == 0


def test_approximately_25_hour_gap_on_consecutive_calendar_dates_is_back_to_back():
    # 2025-10-10 23:30 UTC -> 2025-10-12 00:45 UTC is ~25h15m elapsed but the
    # calendar dates are 10-10 and 10-12 -- NOT consecutive, so NOT a B2B.
    # 2025-10-10 23:30 UTC -> 2025-10-11 00:45 UTC is ~25h15m too, but dates
    # 10-10 -> 10-11 ARE consecutive -- correctly a B2B despite >24h elapsed.
    prior_game = _ingest(_game("G0", "2025-10-10T23:30:00Z", home_forwards=[_skater(100, "P", "C", 3)]))
    snap = feat.hydrate_pregame_snapshot(
        event_id="TARGET", player_id="100", team_id="10", opponent_team_id="20", is_home=True,
        target_provider_season_id="20252026", event_start="2025-10-12T00:45:00Z", as_of="2025-10-11T00:00:00Z",
        history=list(prior_game),
    )
    assert (24 * 60 + 75) / 60.0 > 24  # sanity: >24h elapsed
    assert snap.feature_values["back_to_back"] == 0.0  # 10-10 -> 10-12, not consecutive dates

    snap_consecutive = feat.hydrate_pregame_snapshot(
        event_id="TARGET2", player_id="100", team_id="10", opponent_team_id="20", is_home=True,
        target_provider_season_id="20252026", event_start="2025-10-11T00:45:00Z", as_of="2025-10-10T23:45:00Z",
        history=list(prior_game),
    )
    assert snap_consecutive.feature_values["back_to_back"] == 1.0  # 10-10 -> 10-11, consecutive dates


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
    assert original_snap.event_start == original_start
    assert rescheduled_snap.event_start == rescheduled_start
    assert rescheduled_snap.feature_sample_counts["rolling_sog_per_game_5"] == 5


def test_prior_game_started_but_not_settled_by_as_of_is_excluded():
    # Game started 10-11 but WOW did not retrieve/settle its box score until
    # 10-13 (available_at) -- as_of=10-12 must NOT see it, even though the
    # game chronologically started before the target.
    settled_late = _ingest(
        _game("LATE_SETTLE", "2025-10-11T00:00:00Z", home_forwards=[_skater(100, "P", "C", 9)]),
        retrieved_at="2025-10-13T00:00:00Z",
    )
    early_games = _veteran_history(n=5)  # settles at default retrieved_at, well before as_of
    history = early_games + list(settled_late)
    snap = feat.hydrate_pregame_snapshot(
        event_id="TARGET", player_id="100", team_id="10", opponent_team_id="20", is_home=True,
        target_provider_season_id="20252026", event_start="2025-10-14T00:00:00Z", as_of="2025-10-12T00:00:00Z",
        history=history,
    )
    assert "LATE_SETTLE" not in snap.evidence_ids
    assert snap.feature_sample_counts["career_qualifying_games_available"] == 5


def test_settled_before_as_of_is_included():
    settled_late = _ingest(
        _game("LATE_SETTLE", "2025-10-11T00:00:00Z", home_forwards=[_skater(100, "P", "C", 9)]),
        retrieved_at="2025-10-13T00:00:00Z",
    )
    early_games = _veteran_history(n=5)
    history = early_games + list(settled_late)
    snap = feat.hydrate_pregame_snapshot(
        event_id="TARGET", player_id="100", team_id="10", opponent_team_id="20", is_home=True,
        target_provider_season_id="20252026", event_start="2025-10-15T00:00:00Z", as_of="2025-10-14T00:00:00Z",
        history=history,
    )
    assert "LATE_SETTLE" in snap.evidence_ids
    assert snap.feature_sample_counts["career_qualifying_games_available"] == 6


def test_attempted_current_game_leakage_is_rejected():
    history = _veteran_history()
    leaking_record = _ingest(_game(
        "TARGET", "2025-10-01T00:00:00Z", home_forwards=[_skater(100, "P", "C", 99)],
    ))
    poisoned_history = history + list(leaking_record)
    snap = feat.hydrate_pregame_snapshot(
        event_id="TARGET", player_id="100", team_id="10", opponent_team_id="20", is_home=True,
        target_provider_season_id="20252026", event_start="2025-10-13T00:00:00Z", as_of="2025-10-12T00:00:00Z",
        history=poisoned_history,
    )
    assert "TARGET" not in snap.evidence_ids
    assert snap.feature_sample_counts["career_qualifying_games_available"] == 6


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
    history: list = []
    snap = feat.hydrate_pregame_snapshot(
        event_id="TARGET", player_id="100", team_id="10", opponent_team_id="20", is_home=True,
        target_provider_season_id="20252026", event_start="2025-10-13T00:00:00Z", as_of="2025-10-12T00:00:00Z",
        history=history,
    )
    assert "rest_days" in snap.missing_features
    assert snap.missing_feature_reasons["rest_days"] == "NO_PRIOR_TEAM_GAME_AVAILABLE"


def test_old_five_game_history_is_sample_sufficient_but_not_falsely_fresh():
    old_dates = ["2025-06-01T00:00:00Z", "2025-06-03T00:00:00Z", "2025-06-05T00:00:00Z", "2025-06-07T00:00:00Z", "2025-06-09T00:00:00Z"]
    history = _veteran_history(n=5, start_dates=old_dates)
    snap = feat.hydrate_pregame_snapshot(
        event_id="TARGET", player_id="100", team_id="10", opponent_team_id="20", is_home=True,
        target_provider_season_id="20252026", event_start="2025-10-13T00:00:00Z", as_of="2025-10-12T00:00:00Z",
        history=history,
    )
    assert snap.feature_sample_counts["rolling_sog_per_game_5"] == 5
    assert snap.sample_sufficiency_status == feat.SAMPLE_SUFFICIENT
    assert snap.freshness_status != feat.FRESHNESS_FRESH
    assert snap.freshness_status == feat.FRESHNESS_STALE


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


def test_same_numeric_features_from_different_evidence_ids_hash_differently():
    # Two independent 5-game histories engineered to produce IDENTICAL
    # rolling numeric feature values but from different underlying games
    # (different canonical_game_id -> different evidence_ids).
    history_a = _veteran_history(n=5, start_dates=["2025-09-01T00:00:00Z", "2025-09-03T00:00:00Z", "2025-09-05T00:00:00Z", "2025-09-07T00:00:00Z", "2025-09-09T00:00:00Z"])
    history_b = _veteran_history(n=5, start_dates=["2025-10-01T00:00:00Z", "2025-10-03T00:00:00Z", "2025-10-05T00:00:00Z", "2025-10-07T00:00:00Z", "2025-10-09T00:00:00Z"], game_id_prefix="H")
    kwargs_a = dict(
        event_id="TARGET", player_id="100", team_id="10", opponent_team_id="20", is_home=True,
        target_provider_season_id="20252026", event_start="2025-09-11T00:00:00Z", as_of="2025-09-10T00:00:00Z",
    )
    kwargs_b = dict(kwargs_a, event_start="2025-10-11T00:00:00Z", as_of="2025-10-10T00:00:00Z")
    snap_a = feat.hydrate_pregame_snapshot(history=history_a, **kwargs_a)
    snap_b = feat.hydrate_pregame_snapshot(history=history_b, **kwargs_b)
    # Same shots/toi pattern -> identical rolling feature values...
    assert snap_a.feature_values["rolling_sog_per_game_5"] == snap_b.feature_values["rolling_sog_per_game_5"]
    # ...but different underlying evidence -> different hash.
    assert snap_a.evidence_ids != snap_b.evidence_ids
    assert snap_a.snapshot_hash != snap_b.snapshot_hash


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


def test_precomputed_history_index_matches_raw_history_path():
    history = _veteran_history()
    kwargs = dict(
        event_id="TARGET", player_id="100", team_id="10", opponent_team_id="20", is_home=True,
        target_provider_season_id="20252026", event_start="2025-10-13T00:00:00Z", as_of="2025-10-12T00:00:00Z",
    )
    snap_raw = feat.hydrate_pregame_snapshot(history=history, **kwargs)
    index = feat.build_history_index(history)
    snap_indexed = feat.hydrate_pregame_snapshot(history_index=index, **kwargs)
    assert snap_raw.to_dict() == snap_indexed.to_dict()


def test_hydrate_requires_history_or_index():
    with pytest.raises(feat.NHLSogFeatureError) as exc:
        feat.hydrate_pregame_snapshot(
            event_id="TARGET", player_id="100", team_id="10", opponent_team_id="20", is_home=True,
            target_provider_season_id="20252026", event_start="2025-10-13T00:00:00Z", as_of="2025-10-12T00:00:00Z",
        )
    assert exc.value.code == "NHL_SOG_FEATURE_NO_HISTORY_PROVIDED"
