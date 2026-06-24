import pytest
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../.."))

from gate_engine.board_intake import normalize_row
from gate_engine import market_gate
from gate_engine.labels import DataStatus


def _row(line=25.5):
    return normalize_row({
        "player": "Test", "sport": "NBA", "prop_type": "Points",
        "line": line, "direction": "MORE", "slate_date": "2026-06-24",
    })


def test_no_market_flagged():
    row = _row()
    out = market_gate.run(row)
    assert out["gates"]["market_gate"]["market_status"] == "NO_MARKET_AVAILABLE"
    assert any("NO_MARKET" in b for b in out["blockers"])


def test_verified_when_delta_small():
    row = _row(25.5)
    out = market_gate.run(row, sportsbook_line=25.5)
    assert out["gates"]["market_gate"]["market_status"] == "MARKET_VERIFIED"


def test_edge_detected_when_pp_higher():
    row = _row(25.8)
    out = market_gate.run(row, sportsbook_line=25.5)
    assert out["gates"]["market_gate"]["market_status"] == "MARKET_EDGE_DETECTED"


def test_severe_drift_flagged():
    row = _row(26.0)
    out = market_gate.run(row, sportsbook_line=25.0)
    row2 = _row(30.0)
    out2 = market_gate.run(row2, sportsbook_line=25.0)
    assert out2["gates"]["market_gate"]["market_status"] == "SEVERE_BOARD_VS_BOOK_DRIFT"
    assert any("SEVERE_DRIFT" in b for b in out2["blockers"])


def test_no_vig_calculation():
    row = _row()
    out = market_gate.run(row, sportsbook_line=25.5, clv_entry_price=-110)
    nv = out["gates"]["market_gate"]["no_vig_prob"]
    assert nv is not None
    assert 0.5 < nv < 0.6


def test_clv_status_pending():
    row = _row()
    out = market_gate.run(row, sportsbook_line=25.0, clv_entry_price=-115)
    assert out["gates"]["market_gate"]["clv_status"] == "CLV_PENDING"
