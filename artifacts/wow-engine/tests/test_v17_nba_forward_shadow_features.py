from __future__ import annotations

from datetime import date, timedelta
import random

import pytest

from basketball_team_event_specialist import FEATURE_NAMES, TrainingGame, build_pregame_features
from v17 import nba_current_pregame_scorer as scorer
from v17 import nba_forward_shadow_features as fs


def _corpus(seed: int = 7, days: int = 80) -> list[TrainingGame]:
    rng = random.Random(seed)
    teams = [str(i) for i in range(1, 9)]
    games, n = [], 0
    for d in range(days):
        day = date(2026, 10, 1) + timedelta(days=d)
        pool = teams[:]
        rng.shuffle(pool)
        for i in range(0, len(pool), 2):
            if rng.random() < 0.6:
                n += 1
                hs, as_ = rng.randint(90, 125), rng.randint(90, 125)
                if hs == as_:
                    hs += 1
                games.append(TrainingGame(f"g{n}", "NBA", 2027, day, pool[i], pool[i + 1], hs, as_))
    return games


EVENT = dict(
    official_event_id="espn-1", home_team_id="1", away_team_id="2",
    event_start_time_utc="2026-12-30T00:30:00Z",
    feature_timestamp="2026-12-29T18:00:00Z",
    source_snapshot_id="snap-1", source_snapshot_timestamp="2026-12-29T17:00:00Z",
)


def test_training_serving_parity_on_every_historical_row():
    games = _corpus()
    for row in build_pregame_features(games, "NBA"):
        target = next(g for g in games if g.game_id == row.game_id)
        got = fs.current_feature_values(games, target.home_team_id, target.away_team_id, target.game_date)
        assert got == row.values


def test_pass_package_is_research_only_and_never_publishable():
    pkg = fs.hydrate_nba_current_pregame_features(settled_games=_settled(_corpus()), **EVENT)
    assert pkg["status"] == "PASS"
    assert set(pkg["features"]) == set(FEATURE_NAMES)
    assert pkg["probability_publishable"] is False and pkg["rank_eligible"] is False
    assert pkg["can_execute"] is False
    assert pkg["evidence_grade"] == "RESEARCH_ONLY_NOT_FOR_PUBLICATION"


def test_publication_seam_still_refuses_unattested_package():
    pkg = fs.hydrate_nba_current_pregame_features(settled_games=_settled(_corpus()), **EVENT)
    identity = {"event_id": "espn-1", "home_team_id": "1", "away_team_id": "2"}
    blocker = scorer._feature_blocker(
        pkg, identity, source_snapshot_id="snap-1", event_start_time=EVENT["event_start_time_utc"]
    )
    assert blocker == "NBA_SOURCE_EVENT_REPLAY_NOT_VERIFIED"


def test_same_day_and_future_finals_never_enter_features():
    games = _corpus()
    base = fs.current_feature_values(games, "1", "2", date(2026, 12, 30))
    leak = games + [
        TrainingGame("same", "NBA", 2027, date(2026, 12, 30), "1", "3", 130, 80),
        TrainingGame("fut", "NBA", 2027, date(2026, 12, 31), "2", "3", 130, 80),
    ]
    assert fs.current_feature_values(leak, "1", "2", date(2026, 12, 30)) == base


@pytest.mark.parametrize("n", [0, 4])
def test_insufficient_prior_games_hold_not_guess(n):
    games = [g for g in _corpus() if "1" in (g.home_team_id, g.away_team_id)][:n]
    pkg = fs.hydrate_nba_current_pregame_features(settled_games=_settled(games), **EVENT)
    assert pkg["status"] == "HOLD"
    assert pkg["reason_code"] == "NBA_FORWARD_SHADOW_PRIOR_GAMES_INSUFFICIENT"
    assert pkg["can_execute"] is False


@pytest.mark.parametrize(
    "override,code",
    [
        ({"feature_timestamp": "2026-12-30T00:30:00Z"}, "NBA_FORWARD_SHADOW_NOT_PREGAME"),
        ({"source_snapshot_timestamp": "2026-12-30T01:00:00Z"}, "NBA_FORWARD_SHADOW_NOT_PREGAME"),
        ({"source_snapshot_timestamp": "2026-12-29T19:00:00Z"}, "NBA_FORWARD_SHADOW_SOURCE_AFTER_FEATURE"),
        ({"feature_timestamp": "2026-12-29T18:00:00"}, "NBA_FORWARD_SHADOW_TIMESTAMP_INVALID"),
        ({"source_snapshot_id": ""}, "NBA_FORWARD_SHADOW_IDENTITY_MISSING"),
        ({"away_team_id": "1"}, "NBA_FORWARD_SHADOW_IDENTITY_INVALID"),
    ],
)
def test_typed_holds(override, code):
    pkg = fs.hydrate_nba_current_pregame_features(settled_games=_settled(_corpus()), **{**EVENT, **override})
    assert pkg["status"] == "HOLD" and pkg["reason_code"] == code


def test_dataset_id_is_deterministic_and_history_sensitive():
    games = _corpus()
    a = fs.hydrate_nba_current_pregame_features(settled_games=_settled(games), **EVENT)
    b = fs.hydrate_nba_current_pregame_features(settled_games=list(reversed(_settled(games))), **EVENT)
    assert a["feature_audit"]["dataset_id"] == b["feature_audit"]["dataset_id"]
    changed = [games[0].__class__(**{**games[0].__dict__, "home_score": games[0].home_score + 7})] + games[1:]
    c = fs.hydrate_nba_current_pregame_features(settled_games=_settled(changed), **EVENT)
    if {games[0].home_team_id, games[0].away_team_id} & {"1", "2"}:
        assert c["feature_audit"]["dataset_id"] != a["feature_audit"]["dataset_id"]


# ---- review-finding regressions (CodeRabbit on #2595) ----------------------
from datetime import datetime, timezone  # noqa: E402


def _settled(games, lag_hours=3):
    """Wrap games with a verifiable settlement time: end of game day + lag."""
    return [
        fs.SettledGame(
            game=g,
            settled_at_utc=datetime(g.game_date.year, g.game_date.month, g.game_date.day, 23, 0, tzinfo=timezone.utc)
            + timedelta(hours=lag_hours),
        )
        for g in games
    ]


def _hydrate(settled, **over):
    return fs.hydrate_nba_current_pregame_features(settled_games=settled, **{**EVENT, **over})


def test_result_settled_after_source_snapshot_is_not_used():
    games = _corpus()
    base_settled = _settled(games)
    # Dec 29 final for team 1 settles AFTER the Dec 29 17:00Z source snapshot.
    late = fs.SettledGame(
        game=TrainingGame("late", "NBA", 2027, date(2026, 12, 29), "1", "3", 140, 70),
        settled_at_utc=datetime(2026, 12, 29, 23, 30, tzinfo=timezone.utc),
    )
    a, b = _hydrate(base_settled), _hydrate(base_settled + [late])
    assert a["status"] == b["status"] == "PASS"
    assert a["features"] == b["features"]
    assert a["feature_audit"]["dataset_id"] == b["feature_audit"]["dataset_id"]


def test_missing_or_naive_settlement_time_holds():
    games = _corpus()
    s = _settled(games)
    s[0] = fs.SettledGame(game=s[0].game, settled_at_utc=None)
    team = {"1", "2"}
    relevant = bool(team & {s[0].game.home_team_id, s[0].game.away_team_id})
    pkg = _hydrate(s)
    if relevant:
        assert pkg["status"] == "HOLD" and pkg["reason_code"] == "NBA_FORWARD_SHADOW_SETTLEMENT_TIME_UNVERIFIABLE"
    naive = _settled(games)
    naive[1] = fs.SettledGame(game=naive[1].game, settled_at_utc=datetime(2026, 10, 3, 1, 0))
    if {naive[1].game.home_team_id, naive[1].game.away_team_id} & team:
        assert _hydrate(naive)["reason_code"] == "NBA_FORWARD_SHADOW_SETTLEMENT_TIME_UNVERIFIABLE"


def test_non_nba_rows_are_ignored_everywhere():
    games = _corpus()
    base = _hydrate(_settled(games))
    wnba = [TrainingGame("w1", "WNBA", 2027, date(2026, 12, 28), "1", "2", 99, 50)]
    mixed = _hydrate(_settled(games) + _settled(wnba))
    assert mixed["features"] == base["features"]
    assert mixed["feature_audit"]["dataset_id"] == base["feature_audit"]["dataset_id"]


def test_tied_settled_score_is_typed_hold_not_exception():
    games = _corpus()
    t = TrainingGame("tie", "NBA", 2027, date(2026, 12, 28), "1", "2", 100, 100)
    pkg = _hydrate(_settled(games) + _settled([t]))
    assert pkg["status"] == "HOLD" and pkg["reason_code"] == "NBA_FORWARD_SHADOW_TIED_SETTLED_SCORE"


def test_duplicate_relevant_game_id_is_typed_hold():
    games = _corpus()
    dup = [g for g in games if g.home_team_id == "1" or g.away_team_id == "1"][0]
    pkg = _hydrate(_settled(games) + _settled([dup]))
    assert pkg["status"] == "HOLD" and pkg["reason_code"] == "NBA_FORWARD_SHADOW_DUPLICATE_GAME_ID"


def test_unrelated_errors_propagate():
    with pytest.raises(AttributeError):
        _hydrate([object()])
