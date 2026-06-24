"""
Tests for WOW v16 Claude Audit Closure validators.
Covers all 10 required test scenarios.
"""
import pytest
from datetime import datetime, timezone, timedelta
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../.."))

from gate_engine.audit_closure import (
    make_closure, run_audit_closure,
    validate_required_fields, validate_l5_line_used,
    validate_approval_staleness, validate_edge_vs_friction,
    validate_market_edge_confirmed, validate_source_conflict,
    validate_des_conflict_persistence, validate_power_play_eligibility,
    validate_flex_eligibility, validate_narrative_first_flag,
    validate_structural_failure_count, validate_coin_flip_kill,
    APPROVAL_STALE_HOURS, LINE_MOVEMENT_THRESHOLD,
)


def _now_iso():
    return datetime.now(timezone.utc).isoformat()


def _stale_iso(hours=4):
    return (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()


def _fresh_closure(**overrides):
    """A closure that passes all validators by default."""
    base = make_closure(
        l5_line_used=25.5,
        approval_timestamp=_now_iso(),
        approved_line=25.5,
        current_line=25.5,
        model_prob=0.62,
        slip_type="POWER",
        edge_vs_friction=0.04,
        market_edge_confirmed=True,
        data_provenance="RETRIEVED:Rotowire+SportRadar",
        matchup_grade_source="WOW_MODEL",
        primary_signal="L10_TREND",
        structural_failure_count=0,
        unresolved_conflict_flags=[],
        board_timestamp=_now_iso(),
        market_timestamp=_now_iso(),
        coin_flip_killed=False,
        direction="MORE",
    )
    base.update(overrides)
    return base


# ---------------------------------------------------------------------------
# Scenario 1: Stale approval blocked
# ---------------------------------------------------------------------------
class TestStaleApproval:
    def test_stale_approval_blocked(self):
        c = _fresh_closure(approval_timestamp=_stale_iso(hours=4))
        r = validate_approval_staleness(c)
        assert r["passed"] is False
        assert "STALE" in r["code"]

    def test_fresh_approval_passes(self):
        c = _fresh_closure(approval_timestamp=_now_iso())
        r = validate_approval_staleness(c)
        assert r["passed"] is True

    def test_missing_timestamp_blocked(self):
        c = _fresh_closure(approval_timestamp=None)
        r = validate_approval_staleness(c)
        assert r["passed"] is False

    def test_exactly_3h_is_blocked(self):
        ts = (datetime.now(timezone.utc) - timedelta(hours=APPROVAL_STALE_HOURS, seconds=1)).isoformat()
        c = _fresh_closure(approval_timestamp=ts)
        r = validate_approval_staleness(c)
        assert r["passed"] is False


# ---------------------------------------------------------------------------
# Scenario 2: L5 line mismatch killed
# ---------------------------------------------------------------------------
class TestL5LineMismatch:
    def test_mismatch_over_threshold_killed(self):
        c = _fresh_closure(l5_line_used=25.5, current_line=26.5)
        r = validate_l5_line_used(c)
        assert r["passed"] is False
        assert "MISMATCH" in r["code"]

    def test_exact_threshold_killed(self):
        c = _fresh_closure(l5_line_used=25.5, current_line=26.0)
        r = validate_l5_line_used(c)
        assert r["passed"] is False

    def test_within_threshold_passes(self):
        c = _fresh_closure(l5_line_used=25.5, current_line=25.8)
        r = validate_l5_line_used(c)
        assert r["passed"] is True

    def test_missing_l5_line_blocked(self):
        c = _fresh_closure(l5_line_used=None)
        r = validate_l5_line_used(c)
        assert r["passed"] is False


# ---------------------------------------------------------------------------
# Scenario 3: Model Qualified without market edge blocked from Power
# ---------------------------------------------------------------------------
class TestModelQualifiedNoPowerWithoutMarket:
    def test_power_without_market_edge_blocked(self):
        c = _fresh_closure(slip_type="POWER", market_edge_confirmed=False)
        r = validate_market_edge_confirmed(c)
        assert r["passed"] is False
        assert "POWER" in r["code"]
        assert r["ceiling"] == "MODEL_QUALIFIED_HOLD"

    def test_power_with_market_edge_passes(self):
        c = _fresh_closure(slip_type="POWER", market_edge_confirmed=True)
        r = validate_market_edge_confirmed(c)
        assert r["passed"] is True

    def test_flex_without_market_edge_capped(self):
        c = _fresh_closure(slip_type="FLEX", market_edge_confirmed=False)
        r = validate_market_edge_confirmed(c)
        assert r["passed"] is True
        assert r["ceiling"] == "MODEL_QUALIFIED_HOLD"

    def test_power_eligibility_blocked_when_no_market(self):
        c = _fresh_closure(slip_type="POWER", market_edge_confirmed=False)
        r = validate_power_play_eligibility(c)
        assert r["passed"] is False
        assert "MARKET_EDGE_NOT_CONFIRMED" in r["detail"]


# ---------------------------------------------------------------------------
# Scenario 4: edge_vs_friction UNKNOWN capped at Watch
# ---------------------------------------------------------------------------
class TestEdgeVsFriction:
    def test_unknown_capped_at_watch(self):
        c = _fresh_closure(edge_vs_friction="UNKNOWN")
        r = validate_edge_vs_friction(c)
        assert r["passed"] is False
        assert r["ceiling"] == "WATCH"

    def test_none_capped_at_watch(self):
        c = _fresh_closure(edge_vs_friction=None)
        r = validate_edge_vs_friction(c)
        assert r["passed"] is False
        assert r["ceiling"] == "WATCH"

    def test_negative_blocks(self):
        c = _fresh_closure(edge_vs_friction=-0.01, slip_type="POWER")
        r = validate_edge_vs_friction(c)
        assert r["passed"] is False
        assert "NOT_POSITIVE" in r["code"]

    def test_positive_passes(self):
        c = _fresh_closure(edge_vs_friction=0.05)
        r = validate_edge_vs_friction(c)
        assert r["passed"] is True


# ---------------------------------------------------------------------------
# Scenario 5: Line movement 0.5+ triggers rerun
# ---------------------------------------------------------------------------
class TestLineMovement:
    def test_line_moved_half_triggers_rerun(self):
        c = _fresh_closure(
            approval_timestamp=_now_iso(),
            approved_line=25.0,
            current_line=25.5,
        )
        r = validate_approval_staleness(c)
        assert r["passed"] is False
        assert "LINE_MOVED" in r["code"]

    def test_line_moved_less_than_half_ok(self):
        c = _fresh_closure(
            approval_timestamp=_now_iso(),
            approved_line=25.0,
            current_line=25.4,
        )
        r = validate_approval_staleness(c)
        assert r["passed"] is True

    def test_run_audit_marks_rerun_required(self):
        c = _fresh_closure(
            approval_timestamp=_now_iso(),
            approved_line=25.0,
            current_line=25.6,
        )
        out = run_audit_closure(c)
        assert out["rerun_required"] is True


# ---------------------------------------------------------------------------
# Scenario 6: DES conflict persists across sessions
# ---------------------------------------------------------------------------
class TestDESConflictPersistence:
    def test_des_conflict_blocks(self):
        c = _fresh_closure(unresolved_conflict_flags=["DES_CONFLICT:role_split"])
        r = validate_des_conflict_persistence(c)
        assert r["passed"] is False
        assert "PERSISTS" in r["code"]

    def test_des_conflict_in_closure_blocks_approval(self):
        c = _fresh_closure(unresolved_conflict_flags=["DES_CONFLICT:status_stale"])
        out = run_audit_closure(c)
        assert any("DES_CONFLICT" in b for b in out["blockers"])

    def test_no_des_conflict_passes(self):
        c = _fresh_closure(unresolved_conflict_flags=[])
        r = validate_des_conflict_persistence(c)
        assert r["passed"] is True

    def test_other_flags_dont_trigger_des(self):
        c = _fresh_closure(unresolved_conflict_flags=["MARKET_CONFLICT:drift"])
        r = validate_des_conflict_persistence(c)
        assert r["passed"] is True


# ---------------------------------------------------------------------------
# Scenario 7: Manual/engine data conflict blocks approval
# ---------------------------------------------------------------------------
class TestSourceConflict:
    def test_source_conflict_flag_blocks(self):
        c = _fresh_closure(unresolved_conflict_flags=["SOURCE_CONFLICT:Rotowire_vs_ESPN"])
        r = validate_source_conflict(c)
        assert r["passed"] is False

    def test_manual_engine_conflict_blocks(self):
        c = _fresh_closure(unresolved_conflict_flags=["MANUAL_ENGINE:line_discrepancy"])
        r = validate_source_conflict(c)
        assert r["passed"] is False

    def test_provenance_conflict_blocks(self):
        c = _fresh_closure(data_provenance="CONFLICT:Rotowire_vs_NBAStats")
        r = validate_source_conflict(c)
        assert r["passed"] is False

    def test_clean_provenance_passes(self):
        c = _fresh_closure(
            data_provenance="RETRIEVED:Rotowire",
            unresolved_conflict_flags=[],
        )
        r = validate_source_conflict(c)
        assert r["passed"] is True


# ---------------------------------------------------------------------------
# Scenario 8: Narrative-first prop blocked without market verification
# ---------------------------------------------------------------------------
class TestNarrativeFirst:
    def test_narrative_signal_no_market_blocked(self):
        c = _fresh_closure(
            primary_signal="NARRATIVE:back_from_injury",
            market_edge_confirmed=False,
        )
        r = validate_narrative_first_flag(c)
        assert r["passed"] is False
        assert r["ceiling"] == "MODEL_QUALIFIED_HOLD"

    def test_narrative_signal_with_market_passes(self):
        c = _fresh_closure(
            primary_signal="NARRATIVE:back_from_injury",
            market_edge_confirmed=True,
        )
        r = validate_narrative_first_flag(c)
        assert r["passed"] is True

    def test_non_narrative_signal_passes(self):
        c = _fresh_closure(primary_signal="L10_TREND", market_edge_confirmed=False)
        r = validate_narrative_first_flag(c)
        assert r["passed"] is True


# ---------------------------------------------------------------------------
# Scenario 9: 3 structural failure paths kill prop
# ---------------------------------------------------------------------------
class TestStructuralFailureCount:
    def test_three_failures_kill(self):
        c = _fresh_closure(structural_failure_count=3)
        r = validate_structural_failure_count(c)
        assert r["passed"] is False
        assert "KILL" in r["code"]

    def test_more_than_three_kills(self):
        c = _fresh_closure(structural_failure_count=5)
        r = validate_structural_failure_count(c)
        assert r["passed"] is False

    def test_two_failures_survive(self):
        c = _fresh_closure(structural_failure_count=2)
        r = validate_structural_failure_count(c)
        assert r["passed"] is True

    def test_zero_failures_passes(self):
        c = _fresh_closure(structural_failure_count=0)
        r = validate_structural_failure_count(c)
        assert r["passed"] is True

    def test_three_failures_in_full_closure_blocks(self):
        c = _fresh_closure(structural_failure_count=3)
        out = run_audit_closure(c)
        assert any("STRUCTURAL" in b for b in out["blockers"])


# ---------------------------------------------------------------------------
# Scenario 10: Opposite side after coin-flip kill starts fresh
# ---------------------------------------------------------------------------
class TestCoinFlipKill:
    def test_coin_flip_killed_prop_blocked(self):
        c = _fresh_closure(coin_flip_killed=True)
        r = validate_coin_flip_kill(c)
        assert r["passed"] is False
        assert "RESTART_REQUIRED" in r["code"]

    def test_opposite_side_after_kill_blocked(self):
        prior = _fresh_closure(direction="MORE", coin_flip_killed=True)
        current = _fresh_closure(direction="LESS", coin_flip_killed=False)
        r = validate_coin_flip_kill(current, prior_closure=prior)
        assert r["passed"] is False
        assert "OPPOSITE_SIDE" in r["code"]

    def test_same_direction_no_prior_kill_ok(self):
        prior = _fresh_closure(direction="MORE", coin_flip_killed=False)
        current = _fresh_closure(direction="MORE", coin_flip_killed=False)
        r = validate_coin_flip_kill(current, prior_closure=prior)
        assert r["passed"] is True

    def test_no_prior_closure_clean(self):
        c = _fresh_closure(coin_flip_killed=False)
        r = validate_coin_flip_kill(c, prior_closure=None)
        assert r["passed"] is True


# ---------------------------------------------------------------------------
# Integration: full run_audit_closure
# ---------------------------------------------------------------------------
class TestFullAuditClosure:
    def test_clean_closure_passes_all(self):
        c = _fresh_closure()
        out = run_audit_closure(c)
        assert out["passed"] is True
        assert out["blockers"] == []
        assert out["label_ceiling"] is None

    def test_multiple_failures_all_reported(self):
        c = _fresh_closure(
            approval_timestamp=_stale_iso(hours=5),
            edge_vs_friction="UNKNOWN",
            structural_failure_count=3,
        )
        out = run_audit_closure(c)
        assert out["passed"] is False
        assert len(out["blockers"]) >= 3

    def test_required_fields_missing_caught(self):
        c = {}
        r = validate_required_fields(c)
        assert r["passed"] is False
        assert len(r["detail"]) > 0
