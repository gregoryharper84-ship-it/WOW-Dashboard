import pytest
from datetime import date
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../.."))

from gate_engine.board_intake import normalize_row
from gate_engine import slate_validation
from gate_engine.labels import PropLabel


def _row(slate_date=None):
    r = normalize_row({
        "player": "Test Player", "sport": "NBA",
        "prop_type": "Points", "line": 20.5, "direction": "MORE",
    })
    r["slate_date"] = slate_date
    return r


def test_matching_date_passes():
    today = date(2026, 6, 24)
    row = _row("2026-06-24")
    out = slate_validation.run(row, target_date=today)
    assert out["gates"]["slate_validation"]["passed"] is True
    assert out["terminal_label"] is None


def test_wrong_date_purged():
    today = date(2026, 6, 24)
    row = _row("2026-06-23")
    out = slate_validation.run(row, target_date=today)
    assert out["gates"]["slate_validation"]["passed"] is False
    assert out["terminal_label"] == PropLabel.SLATE_PURGE.value


def test_missing_date_purged():
    today = date(2026, 6, 24)
    row = _row(None)
    out = slate_validation.run(row, target_date=today)
    assert out["terminal_label"] == PropLabel.SLATE_PURGE.value


def test_unparseable_date_purged():
    today = date(2026, 6, 24)
    row = _row("not-a-date")
    out = slate_validation.run(row, target_date=today)
    assert out["terminal_label"] == PropLabel.SLATE_PURGE.value


def test_alternate_date_format():
    today = date(2026, 6, 24)
    row = _row("06/24/2026")
    out = slate_validation.run(row, target_date=today)
    assert out["gates"]["slate_validation"]["passed"] is True
