import pytest
from datetime import date
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../.."))

from gate_engine.pipeline import run_pipeline, _build_market_enrichment_report
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
    result = run_pipeline(rows, target_date=TODAY, skip_data_contract=True)
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
    result = run_pipeline(rows, target_date=TODAY, enrichment=enrichment, skip_data_contract=True)
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
    result = run_pipeline(rows, target_date=TODAY, skip_data_contract=True)
    label = result["terminal_labels"][0]["label"]
    assert label in (PropLabel.REJECT_DATA_QUALITY.value, PropLabel.SLATE_PURGE.value)


def test_market_enrichment_report_flags_missing_market_data():
    rows = [_good_row()]
    enrichment = {
        "lebron james:points": {
            "game_log": [26, 28, 24, 30, 25, 27, 26, 29, 24, 28],
            "season_log": [25, 26, 27, 24, 28, 26, 25, 27, 26, 28],
        }
    }
    result = run_pipeline(rows, target_date=TODAY, enrichment=enrichment, skip_data_contract=True)
    report = result["market_enrichment_report"]
    assert report["total_rows"] == 1
    assert report["rows_with_all_market_fields_missing"] == 1
    assert report["rows_with_any_market_field"] == 0
    assert report["rows_capped_model_qualified_hold_no_market"] == 1
    assert "Points" in report["blocker_samples_by_prop"]
    assert result["terminal_labels"][0]["label"] == PropLabel.MODEL_QUALIFIED_HOLD.value


def test_market_enrichment_report_counts_supplied_market_data():
    rows = [_good_row()]
    enrichment = {
        "lebron james:points": {
            "game_log": [26, 28, 24, 30, 25, 27, 26, 29, 24, 28],
            "season_log": [25, 26, 27, 24, 28, 26, 25, 27, 26, 28],
            "sportsbook_line": 25.5,
            "best_available": 25.0,
        }
    }
    result = run_pipeline(rows, target_date=TODAY, enrichment=enrichment, skip_data_contract=True)
    report = result["market_enrichment_report"]
    assert report["rows_with_any_market_field"] == 1
    assert report["rows_with_all_market_fields_missing"] == 0
    assert report["rows_capped_model_qualified_hold_no_market"] == 0


# -------------------------------------------------------------------
# Retroactive PR review — Check 2: Null Safety
# -------------------------------------------------------------------

def test_market_enrichment_report_empty_rows():
    report = _build_market_enrichment_report([])
    assert report["total_rows"] == 0
    assert report["rows_with_any_market_field"] == 0
    assert report["rows_with_all_market_fields_missing"] == 0
    assert report["rows_capped_model_qualified_hold_no_market"] == 0
    assert report["rows_without_market_gate_result"] == 0
    assert report["blocker_samples_by_prop"] == {}


def test_market_enrichment_report_row_missing_gates_key():
    # No "gates" key at all — must not raise, and must be explicitly
    # counted as "no market_gate result" rather than silently folded into
    # the market-field counters (this is the follow-up enhancement from
    # the retroactive PR review: rows that never reached market_gate,
    # e.g. because they failed an earlier gate, are now distinguishable
    # from rows that reached market_gate with genuinely empty fields).
    rows = [{"row_id": "r1", "player": "X", "prop_type": "Points"}]
    report = _build_market_enrichment_report(rows)
    assert report["total_rows"] == 1
    assert report["rows_with_any_market_field"] == 0
    assert report["rows_with_all_market_fields_missing"] == 0
    assert report["rows_without_market_gate_result"] == 1


def test_market_enrichment_report_row_missing_blockers_key():
    # "gates" present but "blockers" key absent — must not raise.
    rows = [{
        "row_id": "r1", "player": "X", "prop_type": "Points",
        "gates": {"market_gate": {"sportsbook_line": None, "best_available": None, "consensus_line": None}},
    }]
    report = _build_market_enrichment_report(rows)
    assert report["total_rows"] == 1
    assert report["rows_with_all_market_fields_missing"] == 1
    assert report["rows_capped_model_qualified_hold_no_market"] == 0
    assert report["rows_without_market_gate_result"] == 0


def test_market_enrichment_report_malformed_market_fields():
    # Empty string and non-numeric junk must not crash and must not be
    # miscounted as "present" (empty string) while a genuinely odd but
    # non-empty value is still counted as present.
    rows = [
        {
            "row_id": "r1", "player": "A", "prop_type": "Points", "blockers": [],
            "gates": {"market_gate": {"sportsbook_line": "", "best_available": None, "consensus_line": None}},
        },
        {
            "row_id": "r2", "player": "B", "prop_type": "Points", "blockers": [],
            "gates": {"market_gate": {"sportsbook_line": "not-a-number", "best_available": None, "consensus_line": None}},
        },
        {
            "row_id": "r3", "player": "C", "prop_type": "Points", "blockers": None,
            "gates": {"market_gate": None},
        },
        "not-a-row",
    ]
    report = _build_market_enrichment_report(rows)
    assert report["total_rows"] == 4
    assert report["rows_with_all_market_fields_missing"] == 1  # r1 (empty string treated as missing)
    assert report["rows_with_any_market_field"] == 1           # r2 (malformed-but-non-empty counts as present)
    # r3 (market_gate: None) + "not-a-row" (non-dict) never produced a
    # usable market_gate result.
    assert report["rows_without_market_gate_result"] == 2


# -------------------------------------------------------------------
# Retroactive PR review — Check 6: QA Coverage
# -------------------------------------------------------------------

def test_market_enrichment_report_counts_rows_with_unrelated_data_contract_rejection():
    # A row that is ultimately terminal-labeled REJECT_DATA_QUALITY (missing
    # player) still passes through market_gate.run() earlier in the pipeline
    # loop (only a small set of gates short-circuit with `continue`; this
    # isn't one of them). market_gate.run() unconditionally appends the
    # NO_MARKET_AVAILABLE blocker whenever no market fields are supplied,
    # regardless of what the row's eventual terminal label turns out to be.
    # So this row legitimately shows up as "all market fields missing" AND
    # "capped" at the gate level, even though its final label
    # (REJECT_DATA_QUALITY) is unrelated to the market — the report reflects
    # gate-level market coverage, not final classification. This is worth
    # knowing when reading the report: "capped_model_qualified_hold_no_market"
    # counts rows that *hit the market blocker*, not rows whose final label
    # is literally MODEL_QUALIFIED_HOLD.
    rows = [{
        "player": None, "sport": "NBA", "prop_type": "Points", "line": 10,
        "direction": "MORE", "slate_date": "2026-06-24",
    }]
    result = run_pipeline(rows, target_date=TODAY, skip_data_contract=True)
    assert result["terminal_labels"][0]["label"] == PropLabel.REJECT_DATA_QUALITY.value
    report = result["market_enrichment_report"]
    assert report["total_rows"] == 1
    assert report["rows_with_any_market_field"] == 0
    assert report["rows_with_all_market_fields_missing"] == 1
    assert report["rows_capped_model_qualified_hold_no_market"] == 1


def test_market_enrichment_report_mixed_batch():
    rows = [
        _good_row(player="LeBron James"),          # market-enriched
        _good_row(player="Kobe Bryant"),            # game log, no market
        {
            "player": None, "sport": "NBA", "prop_type": "Points", "line": 10,
            "direction": "MORE", "slate_date": "2026-06-24",
        },  # unrelated rejection
    ]
    enrichment = {
        "lebron james:points": {
            "game_log": [26, 28, 24, 30, 25, 27, 26, 29, 24, 28],
            "sportsbook_line": 25.5,
            "best_available": 25.0,
        },
        "kobe bryant:points": {
            "game_log": [26, 28, 24, 30, 25, 27, 26, 29, 24, 28],
        },
    }
    result = run_pipeline(rows, target_date=TODAY, enrichment=enrichment, skip_data_contract=True)
    report = result["market_enrichment_report"]
    assert report["total_rows"] == 3
    assert report["rows_with_any_market_field"] == 1
    # Kobe (no market) + the rejected row (also evaluated by market_gate,
    # see test above) both count as "all fields missing" at gate level.
    assert report["rows_with_all_market_fields_missing"] == 2
    # Both Kobe and the rejected row hit the NO_MARKET_AVAILABLE blocker at
    # the gate level (market_gate runs unconditionally); the rejected row's
    # final label is REJECT_DATA_QUALITY, unrelated to the market blocker.
    assert report["rows_capped_model_qualified_hold_no_market"] == 2
    assert "Points" in report["blocker_samples_by_prop"]
    assert len(report["blocker_samples_by_prop"]["Points"]) == 2
    sample_players = {s["player"] for s in report["blocker_samples_by_prop"]["Points"]}
    assert sample_players == {"Kobe Bryant", None}
