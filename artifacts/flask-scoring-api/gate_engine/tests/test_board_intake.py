import pytest
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../.."))

from gate_engine.board_intake import normalize_row, normalize_board
from gate_engine.labels import DataStatus


def _good_row():
    return {
        "player": "LeBron James",
        "sport": "NBA",
        "prop_type": "Points",
        "line": 27.5,
        "direction": "MORE",
        "slate_date": "2026-06-24",
        "board_source": "PrizePicks",
    }


def test_valid_row_passes():
    out = normalize_row(_good_row())
    assert out["data_status"] == DataStatus.RETRIEVED.value
    assert out["intake_errors"] == []
    assert out["player"] == "LeBron James"
    assert out["line"] == 27.5


def test_missing_player_flagged():
    row = _good_row()
    del row["player"]
    out = normalize_row(row)
    assert out["data_status"] == DataStatus.INPUT_FAILURE.value
    assert any("MISSING:player" in e for e in out["intake_errors"])


def test_missing_line_flagged():
    row = _good_row()
    del row["line"]
    out = normalize_row(row)
    assert out["data_status"] == DataStatus.INPUT_FAILURE.value
    assert any("MISSING:line" in e for e in out["intake_errors"])


def test_invalid_direction_flagged():
    row = _good_row()
    row["direction"] = "UP"
    out = normalize_row(row)
    assert any("INVALID_DIRECTION" in e for e in out["intake_errors"])


def test_every_row_in_board_output():
    rows = [_good_row(), {"player": "X"}]
    out = normalize_board(rows)
    assert len(out) == 2


def test_row_id_assigned():
    out = normalize_row(_good_row())
    assert out["row_id"]


def test_no_fake_fill():
    row = _good_row()
    out = normalize_row(row)
    assert out["market_line"] is None
    assert out["consensus_line"] is None
