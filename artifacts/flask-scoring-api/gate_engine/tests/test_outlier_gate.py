import pytest
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../.."))

from gate_engine.board_intake import normalize_row
from gate_engine import l5_l10_ledger, outlier_gate


def _row_with_ledger(l5_games, l10_games, prop_type="Points", line=20.5):
    row = normalize_row({
        "player": "Test", "sport": "NBA", "prop_type": prop_type,
        "line": line, "direction": "MORE", "slate_date": "2026-06-24",
    })
    row["gates"]["l5_l10_ledger"] = {
        "passed": True,
        "l5_avg": round(sum(l5_games)/len(l5_games), 2),
        "l10_avg": round(sum(l10_games)/len(l10_games), 2),
        "l5_median": sorted(l5_games)[len(l5_games)//2],
        "l10_median": sorted(l10_games)[len(l10_games)//2],
        "l10_games": l10_games,
        "small_sample_warning": False,
    }
    return row


def test_no_flags_clean_data():
    games = [20, 21, 22, 20, 21, 22, 20, 21, 22, 20]
    row = _row_with_ledger(games[-5:], games)
    out = outlier_gate.run(row)
    assert out["gates"]["outlier_gate"]["any_flag"] is False


def test_l5_l10_gap_flagged():
    l10 = [20, 20, 20, 20, 20, 20, 20, 20, 20, 20]
    l5  = [30, 30, 30, 30, 30]
    row = _row_with_ledger(l5, l10)
    out = outlier_gate.run(row)
    assert out["gates"]["outlier_gate"]["flags"]["l5_l10_gap_flagged"] is True


def test_whole_number_push_risk():
    games = [20, 21, 22, 20, 21, 22, 20, 21, 22, 20]
    row = _row_with_ledger(games[-5:], games, line=20.0)
    out = outlier_gate.run(row)
    assert out["gates"]["outlier_gate"]["flags"]["whole_number_push_risk"] is True


def test_skipped_when_no_ledger():
    row = normalize_row({
        "player": "X", "sport": "NBA", "prop_type": "Points",
        "line": 20.5, "direction": "MORE", "slate_date": "2026-06-24",
    })
    out = outlier_gate.run(row)
    assert out["gates"]["outlier_gate"]["skipped"] is True
