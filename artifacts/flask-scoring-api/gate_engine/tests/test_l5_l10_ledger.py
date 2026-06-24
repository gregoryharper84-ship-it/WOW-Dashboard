import pytest
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../.."))

from gate_engine.board_intake import normalize_row
from gate_engine import l5_l10_ledger
from gate_engine.labels import DataStatus


def _row(line=25.5, direction="MORE"):
    return normalize_row({
        "player": "Test Player", "sport": "NBA",
        "prop_type": "Points", "line": line, "direction": direction,
        "slate_date": "2026-06-24",
    })


def test_sufficient_data_passes():
    row = _row()
    out = l5_l10_ledger.run(row, game_log=[20,22,30,25,28,24,26,23,27,29])
    assert out["gates"]["l5_l10_ledger"]["passed"] is True
    assert out["gates"]["l5_l10_ledger"]["l10_avg"] is not None


def test_no_log_fails():
    row = _row()
    out = l5_l10_ledger.run(row, game_log=None)
    assert out["gates"]["l5_l10_ledger"]["passed"] is False
    assert any("L10" in b for b in out["blockers"])


def test_insufficient_games_fails():
    row = _row()
    out = l5_l10_ledger.run(row, game_log=[20, 22, 23])
    assert out["gates"]["l5_l10_ledger"]["passed"] is False


def test_hit_rate_more():
    row = _row(line=24.0, direction="MORE")
    games = [25, 26, 23, 27, 28, 24, 26, 25, 22, 27]
    out = l5_l10_ledger.run(row, game_log=games)
    hr = out["gates"]["l5_l10_ledger"]["l10_hit_rate"]
    above = sum(1 for g in games if g > 24.0)
    assert hr == round(above / len(games), 3)


def test_hit_rate_less():
    row = _row(line=28.0, direction="LESS")
    games = [20, 22, 30, 25, 28, 24, 26, 23, 27, 29]
    out = l5_l10_ledger.run(row, game_log=games)
    hr = out["gates"]["l5_l10_ledger"]["l10_hit_rate"]
    below = sum(1 for g in games if g < 28.0)
    assert hr == round(below / len(games), 3)


def test_small_sample_flagged():
    row = _row()
    out = l5_l10_ledger.run(row, game_log=[20, 22, 25, 23, 21])
    assert out["gates"]["l5_l10_ledger"]["small_sample_warning"] is True
