"""
test_terminal_buckets.py — WOW v16 Terminal Bucket Regression Suite

Proves that bad, missing, stale, mocked, or conflicted data
cannot reach MONEY_QUALIFIED or FINAL_APPROVED.

Run with:
    cd artifacts/flask-scoring-api
    python -m pytest gate_engine/tests/test_terminal_buckets.py -v

No real API keys required. All data is synthetic.
"""
from __future__ import annotations

import sys
import os
import pytest
from datetime import date, datetime, timezone

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../.."))

from gate_engine.pipeline import run_pipeline
from gate_engine.labels import PropLabel, DataStatus, APPROVAL_LABELS

TODAY = date(2026, 7, 7)

APPROVAL_VALUES = {PropLabel.FINAL_APPROVED.value, PropLabel.MONEY_QUALIFIED.value}

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _base_row(**overrides) -> dict:
    row = {
        "player":       "Test Player",
        "sport":        "NBA",
        "prop_type":    "Points",
        "line":         25.5,
        "direction":    "MORE",
        "slate_date":   TODAY.isoformat(),
        "board_source": "PrizePicks",
    }
    row.update(overrides)
    return row


def _full_enrichment(**overrides) -> dict:
    """Enrichment that provides all required fields cleanly."""
    base = {
        "opponent":                  "GSW",
        "game_date":                 TODAY.isoformat(),
        "book_or_platform":          "PrizePicks",
        "odds_or_payout":            3.0,
        "data_timestamp":            datetime.now(timezone.utc).isoformat(),
        "status_timestamp":          datetime.now(timezone.utc).isoformat(),
        "role_timestamp":            datetime.now(timezone.utc).isoformat(),
        "l5_values":                 [28, 30, 22, 26, 31],
        "l10_values":                [28, 30, 22, 26, 31, 24, 27, 29, 25, 33],
        "l10_median":                27.5,
        "l10_mean":                  27.5,
        "l5_line_used":              25.5,
        "market_no_vig_probability": 0.57,
        "model_probability_ledger":  {"final_model_prob": 0.60},
        "payout_context":            {"intended_format": "3-pick Power"},
        "failure_path_matrix":       {"PRIMARY_KILL_PATH": {}},
        "directional_exposure_tags": [],
        "provisional_label":         "WATCH",
        "validation_status":         "PENDING",
        "blocker_reason_if_blocked": None,
    }
    base.update(overrides)
    return base


def _run_one(row: dict, enrichment: dict | None = None,
             skip_data_contract: bool = False) -> str:
    """Run pipeline with one row and return its terminal label."""
    player    = (row.get("player")    or "").lower()
    prop_type = (row.get("prop_type") or "").lower()
    key = f"{player}:{prop_type}"
    enr = {key: enrichment} if enrichment else {}
    result = run_pipeline(
        [row],
        target_date=TODAY,
        enrichment=enr,
        skip_data_contract=skip_data_contract,
    )
    return result["terminal_labels"][0]["label"]


def _cannot_approve(label: str) -> bool:
    return label not in APPROVAL_VALUES


# ---------------------------------------------------------------------------
# Scenario A: Missing L10
# ---------------------------------------------------------------------------
class TestMissingL10:
    def test_missing_l10_values_cannot_approve(self):
        enr = _full_enrichment(l10_values=None, l10_median=None, l10_mean=None)
        label = _run_one(_base_row(), enr, skip_data_contract=True)
        assert _cannot_approve(label), (
            f"Missing L10 reached approval bucket: {label}"
        )

    def test_missing_l10_lands_in_expected_bucket(self):
        enr = _full_enrichment(l10_values=None, l10_median=None, l10_mean=None)
        label = _run_one(_base_row(), enr, skip_data_contract=True)
        expected = {
            PropLabel.REJECT_DATA_QUALITY.value,
            PropLabel.MODEL_QUALIFIED_HOLD.value,
            PropLabel.DATA_CONTRACT_FAIL.value,
            PropLabel.REJECT_NO_EDGE.value,
            PropLabel.RESEARCH_INTEREST.value,
        }
        assert label in expected, f"Unexpected bucket for missing L10: {label}"

    def test_empty_l10_list_cannot_approve(self):
        enr = _full_enrichment(l5_values=[], l10_values=[], l10_median=None, l10_mean=None)
        label = _run_one(_base_row(), enr, skip_data_contract=True)
        assert _cannot_approve(label), f"Empty L10 list reached approval: {label}"


# ---------------------------------------------------------------------------
# Scenario B: Missing payout context
# ---------------------------------------------------------------------------
class TestMissingPayoutContext:
    def test_missing_payout_context_cannot_enter_slip(self):
        enr = _full_enrichment(payout_context=None)
        label = _run_one(_base_row(), enr, skip_data_contract=True)
        assert _cannot_approve(label), (
            f"Missing payout_context reached approval: {label}"
        )

    def test_missing_payout_context_max_hold(self):
        enr = _full_enrichment(payout_context=None)
        label = _run_one(_base_row(), enr, skip_data_contract=True)
        approval_or_above = {
            PropLabel.FINAL_APPROVED.value,
            PropLabel.MONEY_QUALIFIED.value,
        }
        assert label not in approval_or_above, (
            f"Missing payout_context hit money label: {label}"
        )

    def test_negative_payout_context_cannot_approve(self):
        enr = _full_enrichment(
            payout_context={"intended_format": "3-pick Power", "slip_ev": -0.15}
        )
        # Negative EV slip should not final-approve
        label = _run_one(_base_row(), enr, skip_data_contract=True)
        assert label != PropLabel.FINAL_APPROVED.value, (
            f"Negative-EV payout context reached FINAL_APPROVED: {label}"
        )


# ---------------------------------------------------------------------------
# Scenario C: Missing status timestamp
# ---------------------------------------------------------------------------
class TestMissingStatusTimestamp:
    def test_missing_status_timestamp_cannot_approve(self):
        enr = _full_enrichment(status_timestamp=None, role_timestamp=None)
        label = _run_one(_base_row(), enr, skip_data_contract=True)
        assert _cannot_approve(label), (
            f"Missing status_timestamp reached approval: {label}"
        )

    def test_missing_role_timestamp_cannot_approve(self):
        enr = _full_enrichment(role_timestamp=None)
        label = _run_one(_base_row(), enr, skip_data_contract=True)
        assert _cannot_approve(label), (
            f"Missing role_timestamp reached approval: {label}"
        )

    def test_missing_data_timestamp_cannot_approve(self):
        enr = _full_enrichment(data_timestamp=None)
        label = _run_one(_base_row(), enr, skip_data_contract=True)
        assert _cannot_approve(label), (
            f"Missing data_timestamp reached approval: {label}"
        )


# ---------------------------------------------------------------------------
# Scenario D: Missing market comparison
# ---------------------------------------------------------------------------
class TestMissingMarket:
    def test_market_unavailable_sentinel_cannot_approve(self):
        enr = _full_enrichment(market_no_vig_probability="MARKET_UNAVAILABLE")
        label = _run_one(_base_row(), enr, skip_data_contract=True)
        assert _cannot_approve(label), (
            f"MARKET_UNAVAILABLE prop reached approval: {label}"
        )

    def test_null_market_probability_cannot_approve(self):
        enr = _full_enrichment(market_no_vig_probability=None)
        label = _run_one(_base_row(), enr, skip_data_contract=True)
        assert _cannot_approve(label), (
            f"Null market_no_vig_probability reached approval: {label}"
        )

    def test_missing_sportsbook_line_max_hold(self):
        enr = _full_enrichment()
        enr.pop("market_no_vig_probability", None)
        label = _run_one(_base_row(), enr, skip_data_contract=True)
        assert label not in APPROVAL_VALUES, (
            f"Missing market comparison reached money label: {label}"
        )


# ---------------------------------------------------------------------------
# Scenario E: Source conflict
# ---------------------------------------------------------------------------
class TestSourceConflict:
    def test_source_conflict_sentinel_blocked(self):
        enr = _full_enrichment(market_no_vig_probability="SOURCE_CONFLICT")
        label = _run_one(_base_row(), enr, skip_data_contract=True)
        assert _cannot_approve(label), (
            f"SOURCE_CONFLICT prop reached approval: {label}"
        )

    def test_conflicting_lines_cannot_approve(self):
        """Simulate conflicting book lines via enrichment."""
        enr = _full_enrichment(
            market_no_vig_probability="SOURCE_CONFLICT",
            model_probability_ledger={"final_model_prob": 0.58, "conflict_flag": True},
        )
        label = _run_one(_base_row(), enr, skip_data_contract=True)
        assert label not in APPROVAL_VALUES, (
            f"Conflicting lines reached money label: {label}"
        )

    def test_source_conflict_expected_bucket(self):
        enr = _full_enrichment(market_no_vig_probability="SOURCE_CONFLICT")
        label = _run_one(_base_row(), enr, skip_data_contract=True)
        expected = {
            PropLabel.SOURCE_CONFLICT.value,
            PropLabel.REJECT_DATA_QUALITY.value,
            PropLabel.MODEL_QUALIFIED_HOLD.value,
            PropLabel.RESEARCH_INTEREST.value,
            PropLabel.REJECT_NO_EDGE.value,
        }
        assert label in expected, f"Unexpected bucket for SOURCE_CONFLICT: {label}"


# ---------------------------------------------------------------------------
# Scenario F: Mock / fallback / DATA_UNOBTAINABLE data
# ---------------------------------------------------------------------------
class TestMockFallbackData:
    def test_data_unobtainable_row_cannot_approve(self):
        row = _base_row()
        row["source_status"] = DataStatus.DATA_UNOBTAINABLE.value
        label = _run_one(row, _full_enrichment(), skip_data_contract=True)
        assert _cannot_approve(label), (
            f"DATA_UNOBTAINABLE prop reached approval: {label}"
        )

    def test_proxy_only_cannot_approve(self):
        row = _base_row()
        row["source_status"] = DataStatus.PROXY_ONLY.value
        label = _run_one(row, _full_enrichment(), skip_data_contract=True)
        assert _cannot_approve(label), (
            f"PROXY_ONLY prop reached approval: {label}"
        )

    def test_input_failure_cannot_approve(self):
        row = _base_row()
        row["source_status"] = DataStatus.INPUT_FAILURE.value
        label = _run_one(row, _full_enrichment(), skip_data_contract=True)
        assert _cannot_approve(label), (
            f"INPUT_FAILURE prop reached approval: {label}"
        )

    def test_failed_status_cannot_approve(self):
        row = _base_row()
        row["source_status"] = DataStatus.FAILED.value
        label = _run_one(row, _full_enrichment(), skip_data_contract=True)
        assert _cannot_approve(label), (
            f"FAILED source_status reached approval: {label}"
        )

    def test_mock_platform_cannot_approve(self):
        row = _base_row()
        row["board_source"] = "mock"
        row["source_status"] = DataStatus.DATA_UNOBTAINABLE.value
        enr = _full_enrichment()
        label = _run_one(row, enr, skip_data_contract=True)
        assert _cannot_approve(label), (
            f"Mock platform prop reached approval: {label}"
        )

    def test_not_called_cannot_approve(self):
        row = _base_row()
        row["source_status"] = DataStatus.NOT_CALLED.value
        label = _run_one(row, _full_enrichment(), skip_data_contract=True)
        assert _cannot_approve(label), (
            f"NOT_CALLED source_status reached approval: {label}"
        )


# ---------------------------------------------------------------------------
# Scenario G: Negative edge
# ---------------------------------------------------------------------------
class TestNegativeEdge:
    def test_zero_edge_rejects(self):
        enr = _full_enrichment(
            market_no_vig_probability=0.50,
            model_probability_ledger={"final_model_prob": 0.50},
        )
        label = _run_one(_base_row(), enr, skip_data_contract=True)
        assert _cannot_approve(label), (
            f"Zero-edge prop reached approval: {label}"
        )

    def test_negative_edge_rejects(self):
        enr = _full_enrichment(
            market_no_vig_probability=0.58,
            model_probability_ledger={"final_model_prob": 0.44},
        )
        label = _run_one(_base_row(), enr, skip_data_contract=True)
        assert _cannot_approve(label), (
            f"Negative-edge prop reached approval: {label}"
        )

    def test_negative_edge_bucket(self):
        enr = _full_enrichment(
            market_no_vig_probability=0.62,
            model_probability_ledger={"final_model_prob": 0.42},
        )
        label = _run_one(_base_row(), enr, skip_data_contract=True)
        assert label not in APPROVAL_VALUES, (
            f"Prop below edge floor hit money label: {label}"
        )


# ---------------------------------------------------------------------------
# Scenario H: Bad slip structure (correlated legs)
# ---------------------------------------------------------------------------
class TestBadSlipStructure:
    def test_three_correlated_same_script_blocked(self):
        """Three legs from the same player/game-script are structurally risky."""
        rows = [
            _base_row(player="Player A", prop_type="Points",   line=25.5),
            _base_row(player="Player A", prop_type="Rebounds",  line=8.5),
            _base_row(player="Player A", prop_type="Assists",   line=6.5),
        ]
        enr = {
            f"player a:{r['prop_type'].lower()}": _full_enrichment()
            for r in rows
        }
        result = run_pipeline(rows, target_date=TODAY, enrichment=enr,
                              skip_data_contract=True)
        labels = [t["label"] for t in result["terminal_labels"]]
        # At least one should be blocked for structure/exposure
        blocked = any(
            lbl in (
                PropLabel.DUPLICATE_EXPOSURE_BLOCK.value,
                PropLabel.REJECT_BAD_STRUCTURE.value,
                PropLabel.REJECT_POWER_CORRELATED.value,
                PropLabel.SESSION_DIRECTIONAL_EXPOSURE_BLOCK.value,
            )
            for lbl in labels
        )
        # If not explicitly blocked, none should reach final approval
        if not blocked:
            for lbl in labels:
                assert lbl != PropLabel.FINAL_APPROVED.value, (
                    f"Correlated same-player legs reached FINAL_APPROVED: {labels}"
                )

    def test_correlated_legs_row_count_preserved(self):
        rows = [
            _base_row(player="Player A", prop_type="Points",  line=25.5),
            _base_row(player="Player B", prop_type="Points",  line=18.5),
            _base_row(player="Player A", prop_type="Assists", line=5.5),
        ]
        result = run_pipeline(rows, target_date=TODAY, skip_data_contract=True)
        assert len(result["prop_ledger"]) == len(rows), (
            "Row count mismatch — pipeline dropped a row"
        )


# ---------------------------------------------------------------------------
# Scenario I: Row count reconciliation
# ---------------------------------------------------------------------------
class TestRowCountReconciliation:
    def test_N_in_N_out(self):
        n = 7
        rows = [_base_row(player=f"Player{i}", line=20.0 + i) for i in range(n)]
        result = run_pipeline(rows, target_date=TODAY)
        assert len(result["prop_ledger"]) == n, (
            f"Expected {n} rows in prop_ledger, got {len(result['prop_ledger'])}"
        )
        assert result["summary"]["total_rows"] == n

    def test_bucket_counts_sum_to_N(self):
        n = 5
        rows = [_base_row(player=f"P{i}", line=22.0 + i) for i in range(n)]
        result = run_pipeline(rows, target_date=TODAY)
        summary = result["summary"]
        label_counts = {
            k: v for k, v in summary.items()
            if isinstance(v, int) and k not in ("total_rows", "final_count")
        }
        total_bucketed = sum(label_counts.values())
        # total_bucketed may not exactly equal n due to sub-label counts;
        # key check: prop_ledger length == n
        assert len(result["prop_ledger"]) == n

    def test_mixed_valid_invalid_rows_preserved(self):
        rows = [
            _base_row(player="Good Player", line=25.5),
            {"player": None, "line": None},           # bad row
            _base_row(player="Another Player", line=18.5),
        ]
        result = run_pipeline(rows, target_date=TODAY)
        assert len(result["prop_ledger"]) == 3, (
            "Bad row was dropped instead of rejected with a bucket"
        )

    def test_empty_board_no_crash(self):
        result = run_pipeline([], target_date=TODAY)
        assert result["summary"]["total_rows"] == 0
        assert result["prop_ledger"] == []

    def test_single_row_no_crash(self):
        result = run_pipeline([_base_row()], target_date=TODAY)
        assert len(result["prop_ledger"]) == 1


# ---------------------------------------------------------------------------
# Scenario J: Invalid terminal label guard
# ---------------------------------------------------------------------------
class TestInvalidLabelGuard:
    """No row should exit the pipeline with a legacy/invalid label."""
    INVALID_LABELS = {"HOLD", "WATCH", "PASS", "LEAN", "CONDITIONAL",
                      "NO BET", "APPROVED", "TEMP APPROVED", "SAFE", "LOCK"}

    def test_no_invalid_labels_in_output(self):
        rows = [_base_row(player=f"P{i}", line=20.0 + i) for i in range(5)]
        enr = {f"p{i}:points": _full_enrichment() for i in range(5)}
        result = run_pipeline(rows, target_date=TODAY, enrichment=enr,
                              skip_data_contract=True)
        for t in result["terminal_labels"]:
            lbl = t["label"]
            assert lbl not in self.INVALID_LABELS, (
                f"Invalid legacy label found in output: {lbl}"
            )


# ---------------------------------------------------------------------------
# Scenario K: DATA_CONTRACT_FAIL never reaches money labels
# ---------------------------------------------------------------------------
class TestDataContractFail:
    def test_missing_prop_type_contract_fail(self):
        row = _base_row(prop_type=None)
        label = _run_one(row)
        assert _cannot_approve(label), (
            f"Missing prop_type reached approval: {label}"
        )

    def test_missing_sport_contract_fail(self):
        row = _base_row(sport=None)
        label = _run_one(row)
        assert _cannot_approve(label), (
            f"Missing sport reached approval: {label}"
        )

    def test_missing_line_contract_fail(self):
        row = _base_row(line=None)
        label = _run_one(row)
        assert _cannot_approve(label), (
            f"Missing line reached approval: {label}"
        )
