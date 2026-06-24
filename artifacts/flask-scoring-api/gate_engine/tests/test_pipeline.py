import pytest
from datetime import date
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../.."))

from gate_engine.pipeline import run_pipeline
from gate_engine.labels import PropLabel


TODAY = date(2026, 6, 24)


def _good_row(**kwargs):
    row = {
        "player": "LeBron James",
        "sport": "NBA",
        "prop_type": "Points",
        "line": 25.5,
        "direction": "MORE",
        "slate_date": "2026-06-24",
        "board_source": "PrizePicks",
    }
    row.update(kwargs)
    return row


def test_every_input_row_in_output():
    rows = [_good_row(), _good_row(player="Kobe"), {"player": None}]
    result = run_pipeline(rows, target_date=TODAY)
    assert len(result["prop_ledger"]) == len(rows)
    assert result["summary"]["total_rows"] == len(rows)


def test_stale_date_gets_slate_purge():
    rows = [_good_row(slate_date="2026-06-23")]
    result = run_pipeline(rows, target_date=TODAY)
    assert result["terminal_labels"][0]["label"] == PropLabel.SLATE_PURGE.value


def test_missing_data_not_fake_filled():
    rows = [_good_row()]
    result = run_pipeline(rows, target_date=TODAY)
    row = result["prop_ledger"][0]
    ledger = row["gates"].get("l5_l10_ledger", {})
    assert ledger.get("l10_avg") is None or not ledger.get("passed")


def test_no_play_when_all_reject():
    rows = [_good_row(slate_date="2026-06-23")]
    result = run_pipeline(rows, target_date=TODAY)
    assert result["summary"]["final_count"] == 0
    assert result["summary"]["no_play"] is True


def test_final_approved_with_full_enrichment():
    rows = [_good_row()]
    enrichment = {
        "lebron james:points": {
            "game_log": [26, 28, 24, 30, 25, 27, 26, 29, 24, 28],
            "season_log": [25, 26, 27, 24, 28, 26, 25, 27, 26, 28],
            "sportsbook_line": 25.5,
            "best_available": 25.0,
        }
    }
    result = run_pipeline(rows, target_date=TODAY, enrichment=enrichment)
    label = result["terminal_labels"][0]["label"]
    assert label in (
        PropLabel.FINAL_APPROVED.value,
        PropLabel.MONEY_QUALIFIED.value,
        PropLabel.MARKET_VERIFIED_HOLD.value,
        PropLabel.MODEL_QUALIFIED_HOLD.value,
    )


def test_exposure_report_present():
    rows = [_good_row()]
    result = run_pipeline(rows, target_date=TODAY)
    assert "exposure_report" in result


def test_data_status_ledger_present():
    rows = [_good_row(), _good_row(player="X")]
    result = run_pipeline(rows, target_date=TODAY)
    assert len(result["data_status_ledger"]) == 2


def test_input_failure_row_classified():
    rows = [{"player": None, "line": None}]
    result = run_pipeline(rows, target_date=TODAY)
    label = result["terminal_labels"][0]["label"]
    assert label in (PropLabel.REJECT_DATA_QUALITY.value, PropLabel.SLATE_PURGE.value)
