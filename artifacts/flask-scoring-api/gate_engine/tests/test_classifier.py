import pytest
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../.."))

from gate_engine.board_intake import normalize_row
from gate_engine import classifier
from gate_engine.labels import PropLabel, DataStatus


def _base_row():
    row = normalize_row({
        "player": "Test Player", "sport": "NBA", "prop_type": "Points",
        "line": 25.5, "direction": "MORE", "slate_date": "2026-06-24",
    })
    return row


def _pass_gate(row, gate_name):
    row.setdefault("gates", {})[gate_name] = {"passed": True}


def _fail_gate(row, gate_name):
    row.setdefault("gates", {})[gate_name] = {"passed": False}


def test_slate_purge_terminal():
    row = _base_row()
    row["terminal_label"] = PropLabel.SLATE_PURGE.value
    out = classifier.classify(row)
    assert out["terminal_label"] == PropLabel.SLATE_PURGE.value


def test_data_quality_reject():
    row = _base_row()
    row["data_status"] = DataStatus.FAILED.value
    out = classifier.classify(row)
    assert out["terminal_label"] == PropLabel.REJECT_DATA_QUALITY.value


def test_no_market_yields_model_qualified():
    row = _base_row()
    for g in ["slate_validation", "status_role", "slip_structure", "exposure_gate"]:
        _pass_gate(row, g)
    row["gates"]["l5_l10_ledger"] = {"passed": True, "l10_hit_rate": 0.7}
    row["gates"]["market_gate"] = {
        "passed": True,
        "market_status": "NO_MARKET_AVAILABLE",
        "data_status": "DATA_UNOBTAINABLE",
    }
    row["gates"]["outlier_gate"] = {"passed": True, "any_flag": False}
    row["gates"]["ev_gate"] = {"passed": True, "money_qualified": False, "edge_score": 0.0, "ev_blockers": ["EV:NO_MARKET:MAX_LABEL=MODEL_QUALIFIED_HOLD"]}
    out = classifier.classify(row)
    assert out["terminal_label"] == PropLabel.MODEL_QUALIFIED_HOLD.value


def test_no_ledger_yields_research_interest():
    row = _base_row()
    for g in ["slate_validation", "status_role", "slip_structure", "exposure_gate"]:
        _pass_gate(row, g)
    row["gates"]["l5_l10_ledger"] = {"passed": False}
    out = classifier.classify(row)
    assert out["terminal_label"] == PropLabel.RESEARCH_INTEREST.value


def test_final_approved_requires_all_gates():
    row = _base_row()
    for g in ["slate_validation", "status_role", "slip_structure", "exposure_gate", "l5_l10_ledger"]:
        _pass_gate(row, g)
    row["gates"]["market_gate"] = {
        "passed": True,
        "market_status": "MARKET_VERIFIED",
    }
    row["gates"]["outlier_gate"] = {"passed": True, "any_flag": False}
    row["gates"]["ev_gate"] = {
        "passed": True,
        "money_qualified": True,
        "edge_score": 0.12,
        "ev_blockers": [],
    }
    out = classifier.classify(row)
    assert out["terminal_label"] == PropLabel.FINAL_APPROVED.value


def test_no_edge_rejected():
    row = _base_row()
    for g in ["slate_validation", "status_role", "slip_structure", "exposure_gate", "l5_l10_ledger"]:
        _pass_gate(row, g)
    row["gates"]["market_gate"] = {"passed": True, "market_status": "MARKET_VERIFIED"}
    row["gates"]["outlier_gate"] = {"passed": True, "any_flag": False}
    row["gates"]["ev_gate"] = {
        "passed": True,
        "money_qualified": False,
        "edge_score": None,
        "ev_blockers": ["EV:LOW_L10_HIT_RATE:0.3"],
    }
    out = classifier.classify(row)
    assert out["terminal_label"] == PropLabel.REJECT_NO_EDGE.value
