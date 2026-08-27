from __future__ import annotations

import copy
import sys
from datetime import date
from pathlib import Path

import pytest

# The repository-level pytest root is above artifacts/wow-engine; add the service
# root explicitly so these tests exercise the same local-module import layout used
# by uvicorn from Render's configured rootDir.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import mlb_v2_incremental as inc


def _state(cutoff: str = "2026-08-27"):
    return {
        "cutoff_exclusive": cutoff,
        "results_through": "2026-08-26",
        "strict_prior_date_only": True,
        "team_hist": {},
        "pitcher_hist": {},
        "elo": {"NYA": 1500.0, "HOU": 1500.0},
    }


def _parsed(game_pk: int, home="NYA", away="HOU", home_runs=5, away_runs=3, starter_base=1000):
    def side(team, opponent, is_home, runs, starter_id):
        return {
            "team": team, "opponent": opponent, "is_home": is_home, "runs": runs,
            "hits": 8, "hr": 1, "bb": 3, "so": 9, "tb": 13,
            "bp_out": 9, "bp_er": 1, "bp_so": 4, "bp_bb": 1,
            "bp_pitch": 45, "bp_relief_appearances": 3,
            "starter_id": f"mlbam:{starter_id}", "starter_out": 18,
            "starter_er": 2, "starter_so": 7, "starter_bb": 2, "starter_hr": 1,
            "starter_h": 5, "starter_pitch": 92, "starter_tbf": 24,
        }
    return {
        "gamePk": game_pk, "game_date": "2026-08-27", "game_number": 1,
        "home": side(home, away, True, home_runs, starter_base),
        "away": side(away, home, False, away_runs, starter_base + 1),
        "y": int(home_runs > away_runs),
    }


def _scheduled(game_pk=1, state="Final", detailed="Final", home_score=5, away_score=3):
    return {
        "gamePk": game_pk, "game_date": "2026-08-27", "game_number": 1,
        "abstract_state": state, "detailed_state": detailed,
        "home_team": "NYA", "away_team": "HOU",
        "home_score": home_score, "away_score": away_score,
    }


def test_no_refresh_when_cutoff_matches_target(monkeypatch):
    state = _state()
    called = []
    monkeypatch.setattr(inc, "_schedule_day", lambda d: called.append(d) or [])
    meta = inc.advance_state_to_target(state, date(2026, 8, 27))
    assert meta["status"] == "NOT_NEEDED"
    assert called == []


def test_one_day_refresh_consumes_only_prior_day(monkeypatch):
    state = _state()
    requested = []
    monkeypatch.setattr(inc, "_schedule_day", lambda d: requested.append(d) or [_scheduled(11)])
    monkeypatch.setattr(inc, "_fetch_day_rows", lambda games: [_parsed(11)])
    meta = inc.advance_state_to_target(state, date(2026, 8, 28))
    assert requested == [date(2026, 8, 27)]
    assert date(2026, 8, 28) not in requested
    assert state["cutoff_exclusive"] == "2026-08-28"
    assert state["results_through"] == "2026-08-27"
    assert meta["games_added"] == 1
    assert state["incremental_refresh"]["same_day_results_used"] is False
    assert len(state["team_hist"]["NYA"]) == 1
    assert len(state["pitcher_hist"]["mlbam:1000"]) == 1


def test_nonfinal_prior_day_rolls_back_atomically(monkeypatch):
    state = _state()
    before = copy.deepcopy(state)
    monkeypatch.setattr(inc, "_schedule_day", lambda d: [_scheduled(12, state="Live", detailed="In Progress")])
    with pytest.raises(RuntimeError, match="PRIOR_DAY_NOT_TERMINAL"):
        inc.advance_state_to_target(state, date(2026, 8, 28))
    assert state == before


def test_boxscore_failure_rolls_back_atomically(monkeypatch):
    state = _state()
    before = copy.deepcopy(state)
    monkeypatch.setattr(inc, "_schedule_day", lambda d: [_scheduled(13)])
    monkeypatch.setattr(inc, "_fetch_day_rows", lambda games: (_ for _ in ()).throw(RuntimeError("box fail")))
    with pytest.raises(RuntimeError, match="box fail"):
        inc.advance_state_to_target(state, date(2026, 8, 28))
    assert state == before


def test_explicit_postponed_game_advances_without_invented_result(monkeypatch):
    state = _state()
    monkeypatch.setattr(
        inc,
        "_schedule_day",
        lambda d: [_scheduled(14, state="Preview", detailed="Postponed", home_score=None, away_score=None)],
    )
    monkeypatch.setattr(inc, "_fetch_day_rows", lambda games: pytest.fail("postponed game must not fetch boxscore"))
    meta = inc.advance_state_to_target(state, date(2026, 8, 28))
    assert state["cutoff_exclusive"] == "2026-08-28"
    assert state["results_through"] == "2026-08-26"
    assert meta["games_added"] == 0
    assert meta["explicit_no_result_games_skipped"] == 1


def test_duplicate_gamepk_is_canonicalized(monkeypatch):
    class Response:
        def raise_for_status(self): pass
        def json(self):
            g = {
                "gamePk": 99, "gameType": "R", "officialDate": "2026-08-27", "gameNumber": 1,
                "status": {"abstractGameState": "Final", "detailedState": "Final"},
                "teams": {
                    "home": {"team": {"id": 147}, "score": 5},
                    "away": {"team": {"id": 117}, "score": 3},
                },
            }
            return {"dates": [{"games": [g, dict(g)]}]}
    monkeypatch.setattr(inc.requests, "get", lambda *a, **k: Response())
    rows = inc._schedule_day(date(2026, 8, 27))
    assert len(rows) == 1
    assert rows[0]["gamePk"] == 99


def test_doubleheader_same_date_elo_is_batched_from_pre_day_rating():
    state = _state()
    g1 = _parsed(21, home_runs=5, away_runs=3, starter_base=2000)
    g2 = _parsed(22, home_runs=2, away_runs=4, starter_base=3000)
    # Both games use the same pre-date 1500/1500 expected probability. With one
    # win and one loss, net home-team Elo delta is K*((1-e)+(0-e)).
    expected = inc._elo_expected(1500.0, 1500.0)
    expected_final = 1500.0 + 20.0 * ((1.0 - expected) + (0.0 - expected))
    inc._apply_day(state, date(2026, 8, 27), [g1, g2])
    assert state["elo"]["NYA"] == pytest.approx(expected_final)
    assert len(state["team_hist"]["NYA"]) == 2


def test_gap_limit_fails_without_mutation():
    state = _state("2026-01-01")
    before = copy.deepcopy(state)
    with pytest.raises(RuntimeError, match="GAP_TOO_LARGE"):
        inc.advance_state_to_target(state, date(2026, 8, 28), max_catchup_days=75)
    assert state == before
