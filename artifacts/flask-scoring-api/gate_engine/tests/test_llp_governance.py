"""
Tests for LLP-PATCH-2026-06-27 Execution Governance v16.1
"""
import pytest
from datetime import datetime, timezone, timedelta
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../.."))

from gate_engine.llp_governance import (
    validate_llp_label, validate_price_edge_fields, validate_edge_threshold,
    validate_probability_cap, validate_timing_freshness, validate_steam_protocol,
    validate_contradiction_kills, validate_session_exposure, validate_reapproval,
    validate_calibration_ledger, run_llp_governance, log_calibration_entry,
    LLPLabel, BANNED_AS_FINAL, EDGE_THRESHOLD, MarketType,
    STEAM_RERUN_THRESHOLD, STEAM_DOWNGRADE_THRESHOLD,
    CALIBRATION_LEDGER_FIELDS, PRICE_EDGE_REQUIRED_FIELDS,
)


def _now():
    return datetime.now(timezone.utc).isoformat()


def _ts_ago(minutes=0, hours=0):
    return (datetime.now(timezone.utc) - timedelta(minutes=minutes, hours=hours)).isoformat()


def _ts_future(minutes=0, hours=0):
    return (datetime.now(timezone.utc) + timedelta(minutes=minutes, hours=hours)).isoformat()


def _approved_candidate(**kwargs):
    base = {
        "final_label":        LLPLabel.APPROVED.value,
        "book":               "DraftKings",
        "odds":               -110,
        "line":               47.5,
        "side":               "OVER",
        "market":             "total",
        "timestamp":          _now(),
        # model_timestamp is required by validate_material_staleness
        "model_timestamp":    _now(),
        "model_probability":  0.59,
        "no_vig_probability": 0.52,
        "edge":               0.07,
        "source":             "WOW_MODEL",
        "opener":             48.0,
        "consensus":          0.535,
        "game_start_time":    _ts_future(hours=3),
        "final_lock_confirmed": False,
        "stake":              1.0,
        "full_rerun_completed": False,
        "prior_label":        None,
        "calibration_ledger": {f: "dummy" for f in CALIBRATION_LEDGER_FIELDS},
    }
    base.update(kwargs)
    return base


# ---------------------------------------------------------------------------
# 1. Label validity
# ---------------------------------------------------------------------------
class TestLLPLabel:
    def test_valid_labels_pass(self):
        for label in LLPLabel:
            r = validate_llp_label(label.value)
            assert r["passed"] is True, f"Expected {label.value} to pass"

    def test_banned_labels_rejected(self):
        for banned in BANNED_AS_FINAL:
            r = validate_llp_label(banned)
            assert r["passed"] is False
            assert "BANNED" in r["code"]

    def test_unknown_label_rejected(self):
        r = validate_llp_label("PROBABLY_GOOD")
        assert r["passed"] is False

    def test_missing_label_rejected(self):
        r = validate_llp_label(None)
        assert r["passed"] is False

    def test_lean_is_banned(self):
        r = validate_llp_label("LEAN")
        assert r["passed"] is False

    def test_conditional_is_banned(self):
        r = validate_llp_label("CONDITIONAL")
        assert r["passed"] is False


# ---------------------------------------------------------------------------
# 2. Price edge fields
# ---------------------------------------------------------------------------
class TestPriceEdgeFields:
    def test_approved_requires_all_fields(self):
        c = _approved_candidate()
        r = validate_price_edge_fields(c)
        assert r["passed"] is True

    def test_missing_field_blocks_approved(self):
        c = _approved_candidate()
        del c["book"]
        r = validate_price_edge_fields(c)
        assert r["passed"] is False
        assert "book" in r["detail"]

    def test_watch_label_skips_field_check(self):
        c = {"final_label": LLPLabel.WATCH.value}
        r = validate_price_edge_fields(c)
        assert r["passed"] is True

    def test_scout_label_skips_field_check(self):
        c = {"final_label": LLPLabel.SCOUT.value}
        r = validate_price_edge_fields(c)
        assert r["passed"] is True


# ---------------------------------------------------------------------------
# 3. Edge thresholds
# ---------------------------------------------------------------------------
class TestEdgeThreshold:
    def test_liquid_main_clears(self):
        c = _approved_candidate(market="moneyline", edge=0.02)
        r = validate_edge_threshold(c)
        assert r["passed"] is True

    def test_liquid_main_below_threshold(self):
        c = _approved_candidate(market="moneyline", edge=0.01)
        r = validate_edge_threshold(c)
        assert r["passed"] is False
        assert r["ceiling"] == LLPLabel.SCOUT.value

    def test_wnba_threshold(self):
        c = _approved_candidate(market="WNBA total", edge=0.019)
        r = validate_edge_threshold(c)
        assert r["passed"] is False

        c2 = _approved_candidate(market="WNBA total", edge=0.021)
        r2 = validate_edge_threshold(c2)
        assert r2["passed"] is True

    def test_derivative_threshold(self):
        c = _approved_candidate(market="F5 total", edge=0.024)
        r = validate_edge_threshold(c)
        assert r["passed"] is False

        c2 = _approved_candidate(market="F5 total", edge=0.026)
        r2 = validate_edge_threshold(c2)
        assert r2["passed"] is True

    def test_alt_niche_threshold(self):
        c = _approved_candidate(market="alt spread", edge=0.029)
        r = validate_edge_threshold(c)
        assert r["passed"] is False

    def test_no_vig_comp_max_scout(self):
        c = _approved_candidate()
        c["no_vig_probability"] = None
        c["edge"] = None
        r = validate_edge_threshold(c)
        assert r["passed"] is False
        assert r["ceiling"] == LLPLabel.SCOUT.value

    def test_edge_computed_from_probs(self):
        c = _approved_candidate(market="moneyline", edge=None,
                                model_probability=0.58, no_vig_probability=0.54)
        r = validate_edge_threshold(c)
        assert r["passed"] is True

    def test_watch_label_skips_edge(self):
        c = _approved_candidate(final_label=LLPLabel.WATCH.value, edge=0.001)
        r = validate_edge_threshold(c)
        assert r["passed"] is True


# ---------------------------------------------------------------------------
# 4. Probability caps
# ---------------------------------------------------------------------------
class TestProbabilityCap:
    def test_below_52_is_reject(self):
        c = _approved_candidate(model_probability=0.51, final_label=LLPLabel.PLAYABLE.value)
        r = validate_probability_cap(c)
        assert r["passed"] is False
        assert r["ceiling"] == LLPLabel.REJECT.value

    def test_52_to_54_is_watch(self):
        c = _approved_candidate(model_probability=0.53, final_label=LLPLabel.PLAYABLE.value)
        r = validate_probability_cap(c)
        assert r["passed"] is False
        assert r["ceiling"] == LLPLabel.WATCH.value

    def test_55_to_57_is_playable(self):
        c = _approved_candidate(model_probability=0.56, final_label=LLPLabel.PLAYABLE.value)
        r = validate_probability_cap(c)
        assert r["passed"] is True

    def test_55_to_57_cannot_be_approved(self):
        c = _approved_candidate(model_probability=0.56, final_label=LLPLabel.APPROVED.value)
        r = validate_probability_cap(c)
        assert r["passed"] is False
        assert r["ceiling"] == LLPLabel.PLAYABLE.value

    def test_58_60_approved_eligible(self):
        c = _approved_candidate(model_probability=0.59, final_label=LLPLabel.APPROVED.value)
        r = validate_probability_cap(c)
        assert r["passed"] is True

    def test_missing_prob_blocked(self):
        c = _approved_candidate(model_probability=None)
        r = validate_probability_cap(c)
        assert r["passed"] is False


# ---------------------------------------------------------------------------
# 5. Timing freshness
# ---------------------------------------------------------------------------
class TestTimingFreshness:
    def test_fresh_line_3h_out_passes(self):
        c = _approved_candidate(
            timestamp=_ts_ago(minutes=10),
            game_start_time=_ts_future(hours=3),
        )
        r = validate_timing_freshness(c)
        assert r["passed"] is True

    def test_stale_line_3h_out_fails(self):
        c = _approved_candidate(
            timestamp=_ts_ago(minutes=35),
            game_start_time=_ts_future(hours=3),
        )
        r = validate_timing_freshness(c)
        assert r["passed"] is False
        assert "STALE" in r["code"]
        assert r["ceiling"] == LLPLabel.WATCH.value

    def test_no_final_lock_within_30m(self):
        c = _approved_candidate(
            timestamp=_ts_ago(minutes=1),
            game_start_time=_ts_future(minutes=20),
            final_lock_confirmed=False,
        )
        r = validate_timing_freshness(c)
        assert r["passed"] is False
        assert "FINAL_LOCK" in r["code"]
        assert r["ceiling"] == LLPLabel.WATCH.value

    def test_final_lock_within_30m_passes(self):
        c = _approved_candidate(
            timestamp=_ts_ago(minutes=1),
            game_start_time=_ts_future(minutes=20),
            final_lock_confirmed=True,
        )
        r = validate_timing_freshness(c)
        assert r["passed"] is True

    def test_no_timestamp_blocked(self):
        c = _approved_candidate(timestamp=None)
        r = validate_timing_freshness(c)
        assert r["passed"] is False


# ---------------------------------------------------------------------------
# 6. Steam protocol
# ---------------------------------------------------------------------------
class TestSteamProtocol:
    def test_no_drift_passes(self):
        c = _approved_candidate(consensus_implied_drift=0.005)
        r = validate_steam_protocol(c)
        assert r["passed"] is True

    def test_rerun_threshold_triggers(self):
        c = _approved_candidate(consensus_implied_drift=STEAM_RERUN_THRESHOLD)
        r = validate_steam_protocol(c)
        assert r["passed"] is False
        assert "RERUN" in r["code"]

    def test_downgrade_threshold_triggers(self):
        c = _approved_candidate(consensus_implied_drift=STEAM_DOWNGRADE_THRESHOLD)
        r = validate_steam_protocol(c)
        assert r["passed"] is False
        assert "DOWNGRADE" in r["code"]
        assert r["ceiling"] == LLPLabel.WATCH.value

    def test_no_drift_field_skipped(self):
        c = _approved_candidate()
        c.pop("consensus_implied_drift", None)
        r = validate_steam_protocol(c)
        assert r["passed"] is True

    def test_steam_below_rerun_threshold_ok(self):
        c = _approved_candidate(consensus_implied_drift=0.010)
        r = validate_steam_protocol(c)
        assert r["passed"] is True


# ---------------------------------------------------------------------------
# 7. Contradiction hard kills
# ---------------------------------------------------------------------------
class TestContradictionKills:
    def test_no_kills_passes(self):
        c = _approved_candidate()
        r = validate_contradiction_kills(c)
        assert r["passed"] is True

    def test_market_move_against_thesis_kills(self):
        c = _approved_candidate(market_move_against_thesis=True)
        r = validate_contradiction_kills(c)
        assert r["passed"] is False
        assert "market_move_against_thesis" in r["detail"]

    def test_source_conflict_kills(self):
        c = _approved_candidate(source_conflict=True)
        r = validate_contradiction_kills(c)
        assert r["passed"] is False

    def test_wrong_slate_kills(self):
        c = _approved_candidate(wrong_slate=True)
        r = validate_contradiction_kills(c)
        assert r["passed"] is False

    def test_stale_price_kills(self):
        c = _approved_candidate(stale_price=True)
        r = validate_contradiction_kills(c)
        assert r["passed"] is False


# ---------------------------------------------------------------------------
# 8. Session exposure
# ---------------------------------------------------------------------------
class TestSessionExposure:
    def test_normal_bet_passes(self):
        c = _approved_candidate(stake=1.0)
        s = {"bets_today": 1, "units_today": 0.5}
        r = validate_session_exposure(c, s)
        assert r["passed"] is True

    def test_max_bets_blocks(self):
        c = _approved_candidate(stake=1.0)
        s = {"bets_today": 3}
        r = validate_session_exposure(c, s)
        assert r["passed"] is False
        assert "MAX_BETS" in r["detail"]

    def test_daily_cap_breach_blocks(self):
        c = _approved_candidate(stake=1.0)
        s = {"bets_today": 0, "units_today": 1.4}
        r = validate_session_exposure(c, s)
        assert r["passed"] is False
        assert "DAILY_CAP" in r["detail"]

    def test_game_cap_breach_blocks(self):
        c = _approved_candidate(stake=0.8)
        s = {"bets_today": 0, "units_today": 0.0, "units_this_game": 0.5}
        r = validate_session_exposure(c, s)
        assert r["passed"] is False
        assert "GAME_CAP" in r["detail"]

    def test_same_script_breach_blocks(self):
        c = _approved_candidate(stake=1.0)
        s = {"bets_today": 0, "units_today": 0.0, "units_same_script": 0.5}
        r = validate_session_exposure(c, s)
        assert r["passed"] is False
        assert "SAME_SCRIPT" in r["detail"]

    def test_duplicate_same_side_blocks(self):
        c = _approved_candidate(stake=0.5, duplicate_same_side=True)
        s = {}
        r = validate_session_exposure(c, s)
        assert r["passed"] is False

    def test_watch_skips_exposure(self):
        c = _approved_candidate(final_label=LLPLabel.WATCH.value, stake=5.0)
        s = {"bets_today": 10}
        r = validate_session_exposure(c, s)
        assert r["passed"] is True


# ---------------------------------------------------------------------------
# 9. Re-approval rules
# ---------------------------------------------------------------------------
class TestReapproval:
    def test_watch_to_approved_without_rerun_blocked(self):
        c = _approved_candidate(
            prior_label=LLPLabel.WATCH.value,
            final_label=LLPLabel.APPROVED.value,
            full_rerun_completed=False,
        )
        r = validate_reapproval(c)
        assert r["passed"] is False
        assert "WITHOUT_RERUN" in r["detail"]

    def test_watch_to_approved_with_rerun_passes(self):
        c = _approved_candidate(
            prior_label=LLPLabel.WATCH.value,
            final_label=LLPLabel.APPROVED.value,
            full_rerun_completed=True,
        )
        r = validate_reapproval(c)
        assert r["passed"] is True

    def test_approved_with_material_change_must_reject(self):
        c = _approved_candidate(
            prior_label=LLPLabel.APPROVED.value,
            final_label=LLPLabel.APPROVED.value,
            material_change_flagged=True,
        )
        r = validate_reapproval(c)
        assert r["passed"] is False

    def test_chase_without_ev_blocked(self):
        c = _approved_candidate(is_chase_or_hedge=True, standalone_positive_ev=False)
        r = validate_reapproval(c)
        assert r["passed"] is False
        assert "CHASE" in r["detail"]

    def test_chase_with_ev_passes(self):
        c = _approved_candidate(is_chase_or_hedge=True, standalone_positive_ev=True)
        r = validate_reapproval(c)
        assert r["passed"] is True

    def test_fresh_approval_passes(self):
        c = _approved_candidate(prior_label=None, full_rerun_completed=False)
        r = validate_reapproval(c)
        assert r["passed"] is True


# ---------------------------------------------------------------------------
# 10. Calibration ledger
# ---------------------------------------------------------------------------
class TestCalibrationLedger:
    def test_complete_ledger_passes(self):
        c = _approved_candidate()
        r = validate_calibration_ledger(c)
        assert r["passed"] is True

    def test_missing_ledger_fails(self):
        c = _approved_candidate(calibration_ledger={})
        r = validate_calibration_ledger(c)
        assert r["passed"] is False

    def test_no_ledger_key_fails(self):
        c = _approved_candidate()
        del c["calibration_ledger"]
        r = validate_calibration_ledger(c)
        assert r["passed"] is False

    def test_partial_ledger_fails(self):
        c = _approved_candidate(calibration_ledger={"date": "2026-06-27"})
        r = validate_calibration_ledger(c)
        assert r["passed"] is False
        assert "Missing" in r["detail"]


# ---------------------------------------------------------------------------
# Integration: run_llp_governance
# ---------------------------------------------------------------------------
class TestFullGovernance:
    def test_clean_candidate_passes(self):
        c = _approved_candidate()
        out = run_llp_governance(c)
        assert out["passed"] is True
        assert out["can_approve_bets"] is False

    def test_multiple_failures_all_reported(self):
        c = _approved_candidate(
            model_probability=0.50,
            edge=0.001,
            market_move_against_thesis=True,
        )
        out = run_llp_governance(c)
        assert out["passed"] is False
        assert len(out["blockers"]) >= 2

    def test_effective_label_capped(self):
        c = _approved_candidate(
            model_probability=0.53,
            final_label=LLPLabel.APPROVED.value,
        )
        out = run_llp_governance(c)
        assert _label_rank(out["effective_label"]) <= _label_rank(LLPLabel.WATCH.value)

    def test_banned_label_blocked_in_full_run(self):
        c = _approved_candidate(final_label="LEAN")
        out = run_llp_governance(c)
        assert any("BANNED" in b or "UNKNOWN" in b for b in out["blockers"])

    def test_can_approve_bets_always_false(self):
        c = _approved_candidate()
        out = run_llp_governance(c)
        assert out["can_approve_bets"] is False


def _label_rank(label):
    from gate_engine.llp_governance import LABEL_ORDER, LLPLabel
    for i, l in enumerate(LABEL_ORDER):
        if l.value == label:
            return i
    return -1


# ---------------------------------------------------------------------------
# RC2 — Opener unavailable does not block CLV grading
# ---------------------------------------------------------------------------
class TestCalibrationLedgerRC2:
    """Required Correction 2: opener missing ≠ CLV blocked."""

    def _ledger_all_except_opener(self):
        """All 20 required fields present, opener absent."""
        return {f: "dummy" for f in CALIBRATION_LEDGER_FIELDS}

    def test_opener_missing_still_passes(self):
        """opener absent → OPENER_UNAVAILABLE note, but passed=True."""
        c = _approved_candidate(calibration_ledger=self._ledger_all_except_opener())
        r = validate_calibration_ledger(c)
        assert r["passed"] is True
        assert "OPENER_UNAVAILABLE" in r["detail"]

    def test_opener_present_passes_cleanly(self):
        """opener present → CALIBRATION_LEDGER_COMPLETE, no notes."""
        ledger = {f: "dummy" for f in CALIBRATION_LEDGER_FIELDS}
        ledger["opener"] = 48.0
        c = _approved_candidate(calibration_ledger=ledger)
        r = validate_calibration_ledger(c)
        assert r["passed"] is True
        assert r["code"] == "CALIBRATION_LEDGER_COMPLETE"

    def test_close_missing_blocks_clv_grading(self):
        """close absent → NO_CLV_GRADING in detail (blocks CLV grading)."""
        ledger = {f: "dummy" for f in CALIBRATION_LEDGER_FIELDS}
        del ledger["close"]
        c = _approved_candidate(calibration_ledger=ledger)
        r = validate_calibration_ledger(c)
        assert r["passed"] is False
        assert "close" in r["detail"]

    def test_opener_and_close_both_missing(self):
        """Both opener and close missing → fails (close is required)."""
        ledger = {f: "dummy" for f in CALIBRATION_LEDGER_FIELDS}
        del ledger["close"]
        c = _approved_candidate(calibration_ledger=ledger)
        r = validate_calibration_ledger(c)
        assert r["passed"] is False

    def test_only_opener_missing_does_not_block_unit_scaling(self):
        """Unit scaling requires 20 core fields — opener absence alone never blocks."""
        ledger = {f: "dummy" for f in CALIBRATION_LEDGER_FIELDS}
        c = _approved_candidate(calibration_ledger=ledger)
        r = validate_calibration_ledger(c)
        # passed=True means unit scaling is allowed
        assert r["passed"] is True


# ---------------------------------------------------------------------------
# TU1 — FULL_FRACTIONAL_KELLY_ELIGIBLE constant exists and is not FULL_KELLY
# ---------------------------------------------------------------------------
class TestFullFractionalKellyEligible:
    def test_constant_exists(self):
        from gate_engine.llp_governance import FULL_FRACTIONAL_KELLY_ELIGIBLE
        assert "FULL_FRACTIONAL_KELLY_ELIGIBLE" in FULL_FRACTIONAL_KELLY_ELIGIBLE

    def test_constant_not_full_kelly(self):
        from gate_engine.llp_governance import FULL_FRACTIONAL_KELLY_ELIGIBLE
        assert "FULL_KELLY" not in FULL_FRACTIONAL_KELLY_ELIGIBLE.split("—")[0].strip()

    def test_graduation_tiers_present(self):
        from gate_engine.llp_governance import CALIBRATION_GRADUATION_TIERS
        assert "100+ candidates" in CALIBRATION_GRADUATION_TIERS
        tier = CALIBRATION_GRADUATION_TIERS["100+ candidates"]
        assert "FULL_FRACTIONAL_KELLY_ELIGIBLE" in tier

    def test_all_graduation_tiers_defined(self):
        from gate_engine.llp_governance import CALIBRATION_GRADUATION_TIERS
        assert len(CALIBRATION_GRADUATION_TIERS) == 4


# ---------------------------------------------------------------------------
# TU3 — LLP_PLAYABLE hard stake caps
# ---------------------------------------------------------------------------
class TestPlayableStakeCaps:
    """LLP_PLAYABLE cannot become a backdoor full-stake bet."""

    def _playable_candidate(self, stake=0.5, **kwargs):
        c = _approved_candidate(
            final_label=LLPLabel.PLAYABLE.value,
            model_probability=0.56,
            stake=stake,
            **kwargs,
        )
        return c

    def test_pre25_cap_enforced(self):
        """Before 25 candidates: max 0.25u."""
        c = self._playable_candidate(stake=0.30)
        session = {"candidates_logged": 10, "bets_today": 0, "units_today": 0.0,
                   "units_this_game": 0.0, "units_same_script": 0.0}
        r = validate_session_exposure(c, session=session)
        assert r["passed"] is False
        assert "PLAYABLE_STAKE_CAP" in r["detail"]

    def test_pre25_cap_at_limit_passes(self):
        """Exactly at 0.25u cap passes."""
        c = self._playable_candidate(stake=0.25)
        session = {"candidates_logged": 10, "bets_today": 0, "units_today": 0.0,
                   "units_this_game": 0.0, "units_same_script": 0.0}
        r = validate_session_exposure(c, session=session)
        assert r["passed"] is True

    def test_pre100_cap_enforced(self):
        """25–99 candidates: max 0.50u."""
        c = self._playable_candidate(stake=0.60)
        session = {"candidates_logged": 50, "bets_today": 0, "units_today": 0.0,
                   "units_this_game": 0.0, "units_same_script": 0.0}
        r = validate_session_exposure(c, session=session)
        assert r["passed"] is False
        assert "PLAYABLE_STAKE_CAP" in r["detail"]

    def test_pre100_cap_at_limit_passes(self):
        """Exactly at 0.50u cap passes (25–99 candidates)."""
        c = self._playable_candidate(stake=0.50)
        session = {"candidates_logged": 50, "bets_today": 0, "units_today": 0.0,
                   "units_this_game": 0.0, "units_same_script": 0.0}
        r = validate_session_exposure(c, session=session)
        assert r["passed"] is True

    def test_reliability_freeze_cap_enforced(self):
        """During Reliability Freeze: max 0.25u regardless of candidate count."""
        c = self._playable_candidate(stake=0.30)
        session = {"candidates_logged": 60, "reliability_freeze": True,
                   "bets_today": 0, "units_today": 0.0,
                   "units_this_game": 0.0, "units_same_script": 0.0}
        r = validate_session_exposure(c, session=session)
        assert r["passed"] is False
        assert "RELIABILITY_FREEZE" in r["detail"]

    def test_100_plus_candidates_no_playable_cap(self):
        """100+ logged candidates — no PLAYABLE cap from count gates."""
        c = self._playable_candidate(stake=0.80)
        session = {"candidates_logged": 120, "bets_today": 0, "units_today": 0.0,
                   "units_this_game": 0.0, "units_same_script": 0.0}
        r = validate_session_exposure(c, session=session)
        # Cap only comes from daily/game/script limits now, not candidate count
        assert "PLAYABLE_STAKE_CAP" not in r.get("detail", "")

    def test_approved_label_unaffected_by_playable_cap(self):
        """LLP_APPROVED is not subject to PLAYABLE stake caps."""
        c = _approved_candidate(stake=1.0)
        session = {"candidates_logged": 5, "bets_today": 0, "units_today": 0.0,
                   "units_this_game": 0.0, "units_same_script": 0.0}
        r = validate_session_exposure(c, session=session)
        assert "PLAYABLE_STAKE_CAP" not in r.get("detail", "")


# ---------------------------------------------------------------------------
# RC1 — CLV formula (market_gate level)
# ---------------------------------------------------------------------------
class TestCLVFormula:
    """Required Correction 1: CLV = closing_implied − entry_implied."""

    def test_underdog_line_moves_to_confirm_thesis(self):
        """Entry +140 (41.7%), close +120 (45.5%) → positive CLV → CLV_BEAT."""
        from gate_engine import market_gate
        assert market_gate._clv_beat(140, 120) is True

    def test_reverse_sign_gives_clv_miss(self):
        """Entry −120 (54.5%), close −110 (52.4%) → negative CLV → CLV_MISS."""
        from gate_engine import market_gate
        # Market moved away: closing implied < entry implied
        assert market_gate._clv_beat(-120, -110) is False

    def test_favorite_line_moves_to_confirm_thesis(self):
        """Entry −110 (52.4%), close −120 (54.5%) → market confirmed → CLV_BEAT."""
        from gate_engine import market_gate
        assert market_gate._clv_beat(-110, -120) is True

    def test_equal_odds_is_miss(self):
        """No movement → closing_implied == entry_implied → CLV_MISS."""
        from gate_engine import market_gate
        assert market_gate._clv_beat(-110, -110) is False

    def test_clv_beat_line_over_beat(self):
        """Total Over 160.5, closes 162.5 → Beat Close."""
        from gate_engine.market_gate import _clv_beat_line
        assert _clv_beat_line(160.5, 162.5, "over") is True

    def test_clv_beat_line_over_miss(self):
        """Total Over 162.5, closes 160.5 → Lost to Close."""
        from gate_engine.market_gate import _clv_beat_line
        assert _clv_beat_line(162.5, 160.5, "over") is False

    def test_clv_beat_line_under_beat(self):
        """Total Under 162.5, closes 160.5 → Beat Close."""
        from gate_engine.market_gate import _clv_beat_line
        assert _clv_beat_line(162.5, 160.5, "under") is True

    def test_clv_beat_line_under_miss(self):
        """Total Under 160.5, closes 162.5 → Lost to Close."""
        from gate_engine.market_gate import _clv_beat_line
        assert _clv_beat_line(160.5, 162.5, "under") is False

    def test_clv_beat_line_spread_favorite_beat(self):
        """Spread favorite −2.5 closes −3.5 → entry(−2.5) > closing(−3.5) → Beat Close."""
        from gate_engine.market_gate import _clv_beat_line
        assert _clv_beat_line(-2.5, -3.5, "favorite") is True

    def test_clv_beat_line_spread_favorite_miss(self):
        """Spread favorite −3.5 closes −2.5 → Lost to Close."""
        from gate_engine.market_gate import _clv_beat_line
        assert _clv_beat_line(-3.5, -2.5, "favorite") is False

    def test_clv_beat_line_spread_underdog_beat(self):
        """Spread underdog +4.5 closes +3.5 → entry(+4.5) > closing(+3.5) → Beat Close."""
        from gate_engine.market_gate import _clv_beat_line
        assert _clv_beat_line(4.5, 3.5, "underdog") is True

    def test_clv_beat_line_spread_underdog_miss(self):
        """Spread underdog +3.5 closes +4.5 → Lost to Close."""
        from gate_engine.market_gate import _clv_beat_line
        assert _clv_beat_line(3.5, 4.5, "underdog") is False
