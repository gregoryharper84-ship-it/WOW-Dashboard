"""Tests for Module G: directional_exposure.py"""
import pytest
from gate_engine.directional_exposure import (
    check_slip, run, SessionExposureLedger,
    SLIP_BLOCK_THRESHOLD, SESSION_WARN_THRESHOLD, SESSION_BLOCK_THRESHOLD,
)
from gate_engine.labels import PropLabel


def _leg(player="Player A", prop="points", tags=None, ev_math=False):
    return {
        "player":                   player,
        "prop_type":                prop,
        "directional_exposure_tags": tags or [],
        "correlation_ev_documented": ev_math,
    }


def _row(tags=None):
    return {
        "blockers":                  [],
        "gates":                     {},
        "terminal_label":            None,
        "directional_exposure_tags": tags or [],
    }


class TestCheckSlipClean:
    def test_no_tags_clean(self):
        legs = [_leg(tags=[]), _leg(tags=[]), _leg(tags=[])]
        result = check_slip(legs)
        assert result["passed"] is True
        assert result["verdict"] == "CLEAN"
        assert result["directional_exposure_count"] == 0

    def test_different_scripts_no_block(self):
        legs = [
            _leg(tags=["fast_pace_over"]),
            _leg(tags=["slow_pace_under"]),
            _leg(tags=["blowout_script"]),
        ]
        result = check_slip(legs)
        assert result["passed"] is True
        assert result["verdict"] in ("CLEAN", "WARNING")
        assert result["directional_exposure_count"] < SLIP_BLOCK_THRESHOLD


class TestCheckSlipWarning:
    def test_two_same_script_is_warning(self):
        legs = [
            _leg(tags=["fast_pace_over"]),
            _leg(tags=["fast_pace_over"]),
            _leg(tags=[]),
        ]
        result = check_slip(legs)
        assert result["verdict"] == "WARNING"
        assert result["passed"] is True
        assert result["directional_exposure_count"] == 2


class TestCheckSlipBlock:
    def test_three_same_script_no_ev_math_blocks(self):
        legs = [
            _leg(tags=["fast_pace_over"]),
            _leg(tags=["fast_pace_over"]),
            _leg(tags=["fast_pace_over"]),
        ]
        result = check_slip(legs)
        assert result["passed"] is False
        assert result["verdict"] == "BLOCK"
        assert result["code"] == "DIRECTIONAL_EXPOSURE_BLOCK"
        assert result["directional_exposure_count"] == SLIP_BLOCK_THRESHOLD

    def test_block_identifies_affected_legs(self):
        legs = [
            _leg("LeBron", "points", ["fast_pace_over"]),
            _leg("Curry", "assists", ["fast_pace_over"]),
            _leg("AD", "rebounds", ["fast_pace_over"]),
        ]
        result = check_slip(legs)
        assert len(result["blocked_legs"]) == SLIP_BLOCK_THRESHOLD

    def test_four_same_script_also_blocks(self):
        legs = [_leg(tags=["pitcher_dominance"])] * 4
        result = check_slip(legs)
        assert result["passed"] is False
        assert result["verdict"] == "BLOCK"

    def test_ev_math_documented_overrides_block(self):
        legs = [
            _leg(tags=["fast_pace_over"], ev_math=True),
            _leg(tags=["fast_pace_over"]),
            _leg(tags=["fast_pace_over"]),
        ]
        result = check_slip(legs)
        assert result["passed"] is True
        assert result["verdict"] == "WARNING"
        assert result["code"] == "DIRECTIONAL_EXPOSURE_EV_OVERRIDE"

    def test_dominant_script_identified(self):
        legs = [
            _leg(tags=["fast_pace_over"]),
            _leg(tags=["fast_pace_over"]),
            _leg(tags=["fast_pace_over"]),
            _leg(tags=["slow_pace_under"]),
        ]
        result = check_slip(legs)
        assert result["dominant_script"] == "fast_pace_over"
        assert result["directional_exposure_count"] == 3


class TestSessionLedger:
    def test_empty_ledger_is_clean(self):
        ledger = SessionExposureLedger()
        snap = ledger.snapshot()
        assert snap["session_verdict"] == "CLEAN"
        assert snap["session_directional_count"] == 0

    def test_accumulate_below_warn_threshold(self):
        ledger = SessionExposureLedger()
        for i in range(SESSION_WARN_THRESHOLD - 1):
            ledger.record({"directional_exposure_tags": ["fast_pace_over"]})
        snap = ledger.snapshot()
        assert snap["session_verdict"] == "CLEAN"

    def test_at_warn_threshold_triggers_warning(self):
        ledger = SessionExposureLedger()
        for i in range(SESSION_WARN_THRESHOLD):
            ledger.record({"directional_exposure_tags": ["fast_pace_over"]})
        snap = ledger.snapshot()
        assert snap["session_verdict"] == "SESSION_WARNING"
        assert snap["code"] == "SESSION_EXPOSURE_WARNING"

    def test_at_block_threshold_triggers_block(self):
        ledger = SessionExposureLedger()
        for i in range(SESSION_BLOCK_THRESHOLD):
            ledger.record({"directional_exposure_tags": ["blowout_script"]})
        snap = ledger.snapshot()
        assert snap["session_verdict"] == "SESSION_BLOCK"
        assert snap["code"] == "SESSION_DIRECTIONAL_EXPOSURE_BLOCK"

    def test_multiple_scripts_tracked_independently(self):
        ledger = SessionExposureLedger()
        ledger.record({"directional_exposure_tags": ["fast_pace_over", "blowout_script"]})
        ledger.record({"directional_exposure_tags": ["fast_pace_over"]})
        snap = ledger.snapshot()
        assert snap["script_counts"]["fast_pace_over"] == 2
        assert snap["script_counts"]["blowout_script"] == 1

    def test_unknown_script_tags_ignored(self):
        ledger = SessionExposureLedger()
        ledger.record({"directional_exposure_tags": ["unknown_custom_tag"]})
        snap = ledger.snapshot()
        assert snap["session_directional_count"] == 0


class TestRunGate:
    def test_run_stamps_gate_on_row(self):
        row = _row(tags=["fast_pace_over"])
        result = run(row)
        assert "directional_exposure" in row["gates"]
        assert result["passed"] is True

    def test_run_records_into_session_ledger(self):
        ledger = SessionExposureLedger()
        for i in range(SESSION_BLOCK_THRESHOLD):
            r = _row(tags=["blowout_script"])
            run(r, session_ledger=ledger)
        snap = ledger.snapshot()
        assert snap["session_verdict"] == "SESSION_BLOCK"

    def test_session_block_appends_blocker_to_row(self):
        ledger = SessionExposureLedger()
        for _ in range(SESSION_BLOCK_THRESHOLD - 1):
            run(_row(tags=["pitcher_dominance"]), session_ledger=ledger)
        # This 6th row should trigger the block
        row = _row(tags=["pitcher_dominance"])
        run(row, session_ledger=ledger)
        assert any("SESSION_DIRECTIONAL_EXPOSURE_BLOCK" in b for b in row["blockers"])

    def test_run_without_ledger_no_session_verdict(self):
        row = _row(tags=["fast_pace_over"])
        result = run(row)
        assert "session_verdict" not in result
