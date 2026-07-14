"""
Tests for WOW-PATCH-MANDATORY-RECONSTRUCTION-v1.0

Covers:
  - acquisition.AcquisitionTracker
  - acquisition.build_run_acquisition_report
  - data_contract.run_intake / run_deferred (new phase-split API)
  - l5_l10_ledger: source_status tracking + season_log fallback
  - pipeline: acquisition_execution_report in output
"""
from __future__ import annotations

import pytest
from gate_engine.acquisition import (
    AcquisitionTracker,
    SourceStatus,
    ReconstructionStatus,
    VERDICT_COMPLETE,
    VERDICT_RUN_INVALID_NOT_CALLED,
    build_run_acquisition_report,
    format_unobtainable_blocker,
)
from gate_engine.data_contract import run_intake, run_deferred, run
from gate_engine.labels import PropLabel
from gate_engine import l5_l10_ledger
from gate_engine.board_intake import normalize_row


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def _base_row():
    return {
        "player": "LeBron James", "sport": "NBA",
        "prop_type": "points", "line": 25.5,
        "direction": "MORE", "blockers": [],
        "gates": {}, "terminal_label": None,
    }


def _full_enrichment():
    return {
        "opponent": "GSW", "game_date": "2026-06-27",
        "book_or_platform": "PrizePicks", "odds_or_payout": 3.0,
        "data_timestamp": "2026-06-27T12:00:00Z",
        "status_timestamp": "2026-06-27T11:30:00Z",
        "role_timestamp": "2026-06-27T11:45:00Z",
        "l5_values": [28, 30, 22, 26, 31],
        "l10_values": [28, 30, 22, 26, 31, 24, 27, 29, 25, 33],
        "l10_median": 27.5, "l10_mean": 27.5, "l5_line_used": 25.5,
        "market_no_vig_probability": 0.54,
        "model_probability_ledger": {"final_model_prob": 0.57},
        "payout_context": {"intended_format": "3-pick Power"},
        "failure_path_matrix": {"PRIMARY_KILL_PATH": {}},
        "directional_exposure_tags": ["fast_pace_over"],
        "provisional_label": "WATCH", "validation_status": "PENDING",
        "blocker_reason_if_blocked": None,
    }


def _ledger_row(line=25.5, direction="MORE"):
    return normalize_row({
        "player": "Test Player", "sport": "NBA",
        "prop_type": "Points", "line": line, "direction": direction,
        "slate_date": "2026-06-24",
    })


# ===========================================================================
# AcquisitionTracker
# ===========================================================================

class TestAcquisitionTracker:
    def test_initial_state_clean(self):
        t = AcquisitionTracker("row-1")
        assert t.row_id == "row-1"
        assert t.not_called_fields() == []
        assert t.is_acquisition_complete() is True

    def test_mark_missing_registers_field(self):
        t = AcquisitionTracker("row-1")
        t.mark_missing_at_intake(["game_log", "opponent"])
        assert "game_log" in t._missing_at_intake
        assert "opponent" in t._missing_at_intake
        assert t.not_called_fields() == ["game_log", "opponent"]

    def test_not_called_before_any_attempt(self):
        t = AcquisitionTracker("row-1")
        t.mark_missing_at_intake(["game_log"])
        assert t.get_final_status("game_log") == SourceStatus.NOT_CALLED
        assert not t.is_acquisition_complete()

    def test_record_attempt_retrieved_marks_complete(self):
        t = AcquisitionTracker("row-1")
        t.mark_missing_at_intake(["game_log"])
        t.record_attempt("game_log", "nba_api", SourceStatus.RETRIEVED, "fetched 10 rows")
        assert t.get_final_status("game_log") == SourceStatus.RETRIEVED
        assert "game_log" in t._recovered
        assert t.is_acquisition_complete() is True

    def test_record_attempt_reconstructed(self):
        t = AcquisitionTracker("row-1")
        t.mark_missing_at_intake(["l5_values"])
        t.record_attempt("l5_values", "season_log", SourceStatus.RECONSTRUCTED)
        assert t.get_final_status("l5_values") == SourceStatus.RECONSTRUCTED
        assert "l5_values" in t._recovered

    def test_record_attempt_proxy_only(self):
        t = AcquisitionTracker("row-1")
        t.mark_missing_at_intake(["projection"])
        t.record_attempt("projection", "internal_model", SourceStatus.PROXY_ONLY)
        assert t.get_final_status("projection") == SourceStatus.PROXY_ONLY
        assert "projection" in t._proxy_only

    def test_priority_order_retrieved_beats_failed(self):
        t = AcquisitionTracker("row-1")
        t.mark_missing_at_intake(["x"])
        t.record_attempt("x", "source_a", SourceStatus.FAILED)
        t.record_attempt("x", "source_b", SourceStatus.RETRIEVED)
        assert t.get_final_status("x") == SourceStatus.RETRIEVED

    def test_build_row_report_complete(self):
        t = AcquisitionTracker("row-1")
        t.mark_missing_at_intake(["game_log"])
        t.record_attempt("game_log", "nba_api", SourceStatus.RETRIEVED)
        rpt = t.build_row_report()
        assert rpt["acquisition_complete"] is True
        assert rpt["acquisition_verdict"] == VERDICT_COMPLETE
        assert "game_log" in rpt["fields_retrieved"]
        assert rpt["fields_not_called"] == []

    def test_build_row_report_not_called(self):
        t = AcquisitionTracker("row-1")
        t.mark_missing_at_intake(["game_log"])
        # No attempt recorded
        rpt = t.build_row_report()
        assert rpt["acquisition_complete"] is False
        assert rpt["acquisition_verdict"] == VERDICT_RUN_INVALID_NOT_CALLED
        assert "game_log" in rpt["fields_not_called"]

    def test_build_row_report_no_missing_fields(self):
        t = AcquisitionTracker("row-1")
        rpt = t.build_row_report()
        assert rpt["acquisition_complete"] is True
        assert rpt["acquisition_verdict"] == VERDICT_COMPLETE
        assert rpt["fields_missing_at_intake"] == []

    def test_mark_unobtainable(self):
        t = AcquisitionTracker("row-1")
        t.mark_missing_at_intake(["x"])
        t.record_attempt("x", "all_sources", SourceStatus.DATA_UNOBTAINABLE)
        t.mark_unobtainable("x")
        assert "x" in t._unobtainable

    def test_duplicate_missing_not_doubled(self):
        t = AcquisitionTracker("row-1")
        t.mark_missing_at_intake(["game_log"])
        t.mark_missing_at_intake(["game_log"])
        assert t._missing_at_intake.count("game_log") == 1


# ===========================================================================
# build_run_acquisition_report
# ===========================================================================

class TestBuildRunAcquisitionReport:
    def _complete_report(self):
        t = AcquisitionTracker("r1")
        t.mark_missing_at_intake(["game_log"])
        t.record_attempt("game_log", "nba_api", SourceStatus.RETRIEVED)
        return t.build_row_report()

    def _incomplete_report(self):
        t = AcquisitionTracker("r2")
        t.mark_missing_at_intake(["projection"])
        # no attempt
        return t.build_row_report()

    def test_all_complete(self):
        rpt = build_run_acquisition_report(
            [self._complete_report(), self._complete_report()]
        )
        assert rpt["acquisition_complete"] is True
        assert rpt["rows_acquisition_complete"] == 2
        assert rpt["rows_run_invalid"] == 0

    def test_partial_complete(self):
        rpt = build_run_acquisition_report(
            [self._complete_report(), self._incomplete_report()]
        )
        assert rpt["acquisition_complete"] is False
        assert rpt["rows_acquisition_complete"] == 1
        assert rpt["rows_run_invalid"] == 1

    def test_empty_list(self):
        rpt = build_run_acquisition_report([])
        assert rpt["acquisition_complete"] is True
        assert rpt["rows_total"] == 0

    def test_failed_source_calls_passed_through(self):
        rpt = build_run_acquisition_report(
            [self._complete_report()],
            failed_source_calls=["l5_l10_ledger:ValueError:bad"],
        )
        assert "l5_l10_ledger:ValueError:bad" in rpt["failed_source_calls"]


# ===========================================================================
# data_contract — run_intake / run_deferred
# ===========================================================================

class TestDataContractRunIntake:
    def test_all_row_fields_present_passes(self):
        row = _base_row()
        result = run_intake(row, _full_enrichment())
        assert result["row_level_fail"] is False
        assert result["enrichment_missing"] == []

    def test_missing_row_field_fails_immediately(self):
        row = _base_row()
        row["player"] = None
        result = run_intake(row, _full_enrichment())
        assert result["row_level_fail"] is True
        assert "player" in result["row_missing"]
        assert row["terminal_label"] == PropLabel.DATA_CONTRACT_FAIL.value

    def test_missing_enrichment_does_not_fail_row(self):
        row = _base_row()
        enr = _full_enrichment()
        del enr["opponent"]
        result = run_intake(row, enr)
        # row is NOT terminated — acquisition ladder should handle it
        assert result["row_level_fail"] is False
        assert "opponent" in result["enrichment_missing"]
        assert row["terminal_label"] is None

    def test_gate_result_recorded(self):
        row = _base_row()
        enr = _full_enrichment()
        del enr["l5_values"]
        run_intake(row, enr)
        assert "data_contract_intake" in row["gates"]
        assert "l5_values" in row["gates"]["data_contract_intake"]["enrichment_missing"]

    def test_team_accepted_for_player(self):
        row = _base_row()
        row.pop("player")
        row["team"] = "LAL"
        result = run_intake(row, _full_enrichment())
        assert result["row_level_fail"] is False

    def test_missing_sport_is_row_level_fail(self):
        row = _base_row()
        row["sport"] = ""
        result = run_intake(row, _full_enrichment())
        assert result["row_level_fail"] is True
        assert "sport" in result["row_missing"]

    def test_all_fields_present_code(self):
        row = _base_row()
        run_intake(row, _full_enrichment())
        code = row["gates"]["data_contract_intake"]["code"]
        assert code == "CONTRACT_PASS_INTAKE"

    def test_enrichment_missing_code(self):
        row = _base_row()
        enr = _full_enrichment()
        del enr["l5_values"]
        run_intake(row, enr)
        code = row["gates"]["data_contract_intake"]["code"]
        assert "FIELD_MISSING_AT_INTAKE" in code


class TestDataContractRunDeferred:
    def test_all_enrichment_present_passes(self):
        row = _base_row()
        result = run_deferred(row, _full_enrichment())
        assert result["passed"] is True
        assert result["code"] == "CONTRACT_PASS"

    def test_missing_enrichment_fails_row(self):
        row = _base_row()
        enr = _full_enrichment()
        del enr["opponent"]
        result = run_deferred(row, enr)
        assert result["passed"] is False
        assert "opponent" in result["missing_fields"]
        assert row["terminal_label"] == PropLabel.DATA_CONTRACT_FAIL.value

    def test_phase_label(self):
        row = _base_row()
        result = run_deferred(row, _full_enrichment())
        assert result.get("phase") == "deferred"

    def test_blocker_appended_on_fail(self):
        row = _base_row()
        enr = _full_enrichment()
        del enr["game_date"]
        run_deferred(row, enr)
        assert any("DATA_CONTRACT_FAIL" in b for b in row["blockers"])

    def test_market_unavailable_sentinel_accepted(self):
        row = _base_row()
        enr = _full_enrichment()
        enr["market_no_vig_probability"] = "MARKET_UNAVAILABLE"
        result = run_deferred(row, enr)
        assert result["passed"] is True

    def test_original_run_still_works(self):
        """The original run() function must remain backward-compatible."""
        row = _base_row()
        result = run(row, _full_enrichment())
        assert result["passed"] is True
        assert result["code"] == "CONTRACT_PASS"

    def test_original_run_fails_on_missing(self):
        row = _base_row()
        enr = _full_enrichment()
        del enr["opponent"]
        result = run(row, enr)
        assert result["passed"] is False
        assert row["terminal_label"] == PropLabel.DATA_CONTRACT_FAIL.value


# ===========================================================================
# l5_l10_ledger — source status tracking + season_log fallback
# ===========================================================================

class TestL5L10LedgerSourceTracking:
    def test_retrieved_status_full_log(self):
        row = _ledger_row()
        l5_l10_ledger.run(row, game_log=[20,22,30,25,28,24,26,23,27,29])
        result = row["gates"]["l5_l10_ledger"]
        assert result["l5_source_status"] == SourceStatus.RETRIEVED
        assert result["l10_source_status"] == SourceStatus.RETRIEVED
        assert result["reconstruction_method"] == "direct_game_log"
        assert result["source_rows_used"] == 10
        assert result["l5_line_used"] == row.get("line")

    def test_reconstructed_status_short_log(self):
        row = _ledger_row()
        l5_l10_ledger.run(row, game_log=[20,22,25,23,21])  # 5 games, <10
        result = row["gates"]["l5_l10_ledger"]
        assert result["l5_source_status"] == SourceStatus.RECONSTRUCTED
        assert result["reconstruction_confidence"] == ReconstructionStatus.RECONSTRUCTED_B_UNCORROBORATED

    def test_source_attempts_logged_primary(self):
        row = _ledger_row()
        l5_l10_ledger.run(row, game_log=[20,22,30,25,28,24,26,23,27,29])
        result = row["gates"]["l5_l10_ledger"]
        attempts = result.get("source_attempts", [])
        assert len(attempts) >= 1
        assert attempts[0]["source"] == "direct_game_log_feed"
        assert attempts[0]["status"] == SourceStatus.RETRIEVED

    def test_no_game_log_not_called_then_unobtainable(self):
        row = _ledger_row()
        l5_l10_ledger.run(row, game_log=None, season_log=None)
        result = row["gates"]["l5_l10_ledger"]
        assert result["passed"] is False
        assert result["l5_source_status"] == SourceStatus.DATA_UNOBTAINABLE
        # Both primary and fallback were attempted/documented
        sources = [a["source"] for a in result.get("source_attempts", [])]
        assert "direct_game_log_feed" in sources
        assert "season_log_reconstruction" in sources

    def test_season_log_fallback_success(self):
        row = _ledger_row()
        season = [20,22,30,25,28,24,26,23,27,29,31,18,22,25]
        l5_l10_ledger.run(row, game_log=None, season_log=season)
        result = row["gates"]["l5_l10_ledger"]
        assert result["passed"] is True
        assert result["reconstruction_method"] == "season_log_reconstruction"
        assert result["l5_source_status"] == SourceStatus.RECONSTRUCTED
        # Full season_log >= 10 games → RECONSTRUCTED_A
        assert result["reconstruction_confidence"] == ReconstructionStatus.RECONSTRUCTED_A

    def test_season_log_fallback_short_gives_b_uncorroborated(self):
        row = _ledger_row()
        # 6 games: >= MIN_GAMES_L5 (4) but < 10
        season = [20, 22, 25, 23, 21, 19]
        l5_l10_ledger.run(row, game_log=None, season_log=season)
        result = row["gates"]["l5_l10_ledger"]
        assert result["passed"] is True
        assert result["reconstruction_confidence"] == ReconstructionStatus.RECONSTRUCTED_B_UNCORROBORATED
        assert any("MODEL_QUALIFIED_HOLD" in b for b in row["blockers"])

    def test_season_log_too_short_falls_through_to_unobtainable(self):
        row = _ledger_row()
        # Only 2 games — below MIN_GAMES_L5
        l5_l10_ledger.run(row, game_log=None, season_log=[20, 22])
        result = row["gates"]["l5_l10_ledger"]
        assert result["passed"] is False
        assert result["l5_source_status"] == SourceStatus.DATA_UNOBTAINABLE

    def test_existing_tests_still_pass(self):
        """Backward compat: all pre-existing result keys still present."""
        row = _ledger_row()
        l5_l10_ledger.run(row, game_log=[20,22,30,25,28,24,26,23,27,29])
        r = row["gates"]["l5_l10_ledger"]
        for k in ["passed", "data_status", "games_available", "l5_avg",
                  "l5_median", "l5_hit_rate", "l10_avg", "l10_median",
                  "l10_hit_rate", "l5_games", "l10_games", "small_sample_warning"]:
            assert k in r, f"missing key: {k}"


# ===========================================================================
# pipeline — acquisition_execution_report in output
# ===========================================================================

class TestPipelineAcquisitionReport:
    def _run(self, rows, enrichment=None):
        from gate_engine.pipeline import run_pipeline
        from datetime import date
        return run_pipeline(
            rows,
            target_date=date(2026, 6, 27),
            enrichment=enrichment or {},
            skip_data_contract=True,
        )

    def _row(self):
        return {
            "player": "LeBron James", "sport": "NBA",
            "prop_type": "points", "line": 25.5,
            "direction": "MORE", "slate_date": "2026-06-27",
        }

    def test_acquisition_report_present_in_output(self):
        result = self._run([self._row()])
        assert "acquisition_execution_report" in result

    def test_acquisition_report_has_required_keys(self):
        result = self._run([self._row()])
        rpt = result["acquisition_execution_report"]
        for k in [
            "fields_missing_at_intake", "fields_retrieved",
            "fields_proxy_only", "fields_unobtainable",
            "failed_source_calls", "fallbacks_executed",
            "acquisition_complete", "rows_acquisition_complete",
            "rows_total", "acquisition_completeness_verdict",
        ]:
            assert k in rpt, f"missing key: {k}"

    def test_acquisition_report_row_count(self):
        result = self._run([self._row(), self._row()])
        rpt = result["acquisition_execution_report"]
        assert rpt["rows_total"] == 2

    def test_no_missing_fields_when_skip_data_contract(self):
        result = self._run([self._row()])
        rpt = result["acquisition_execution_report"]
        # skip_data_contract=True → tracker has no missing_at_intake fields
        assert rpt["fields_missing_at_intake"] == 0


# ===========================================================================
# format_unobtainable_blocker
# ===========================================================================

class TestFormatUnobtainableBlocker:
    def test_format_includes_field_name(self):
        b = format_unobtainable_blocker(
            missing_field="game_log",
            attempted_sources=["nba_api", "bbref"],
            reconstruction_attempted=True,
            reconstruction_result=ReconstructionStatus.RECONSTRUCTION_FAILED,
            proxy_attempted=False,
            final_source_status=SourceStatus.DATA_UNOBTAINABLE,
            approval_impact="DATA_CONTRACT_FAIL",
        )
        assert "game_log" in b
        assert "RECONSTRUCTION_FAILED" in b
        assert "sources_tried=2" in b
