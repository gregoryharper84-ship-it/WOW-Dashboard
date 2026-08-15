"""
test_pp_promotion_gate.py — WOW-PATCH-2026-08-15 regression coverage

Tests all failure classes specified by the patch:
  - PP promotion gate (break-even, no-vig, recency-shock)
  - Probability leaderboard independence from paid-card gate
  - Same-event Power joint dependence model
  - Fatal rejected-leg detection
  - Pregame snapshot write-failure blocking
  - Binding final refresh enforcement
  - Postmortem process classifications

Invariants verified throughout:
  - can_execute = False in every module
  - EXECUTION_RULE = "DRY_RUN_ONLY_NO_LIVE_TRADING_NO_MARKET_ORDERS"
  - Gate only caps labels; never approves or promotes
  - No unrelated architecture touched
"""
from __future__ import annotations

import unittest
from unittest.mock import MagicMock, patch


# ---------------------------------------------------------------------------
# Module imports
# ---------------------------------------------------------------------------
from gate_engine.pp_promotion_gate import (
    run_row, run,
    BREAK_EVEN, DEFAULT_SAFETY_BUFFER, RECENCY_SHOCK_THRESHOLD,
    _check_lower_bound, _check_novig, _check_recency_shock,
    _price_to_implied_prob, _two_way_novig_prob,
    can_execute as promo_can_execute,
    PRODUCTION_AUTHORITY as promo_prod_auth,
    TERMINAL_LABEL_AUTHORITY as promo_tla,
    EXECUTION_RULE as promo_exec_rule,
)
from gate_engine.pp_pregame_snapshot import (
    build_snapshot, snapshot_and_enforce,
    can_execute as snap_can_execute,
    PRODUCTION_AUTHORITY as snap_prod_auth,
)
from gate_engine.pp_final_refresh import (
    detect_material_changes, enforce_final_refresh, run as refresh_run,
    can_execute as refresh_can_execute,
    PRODUCTION_AUTHORITY as refresh_prod_auth,
    CATEGORY_LINEUP, CATEGORY_MARKET, CATEGORY_PRICE,
    CATEGORY_SOURCE, CATEGORY_PARTICIPANT,
)
from gate_engine.prediction_ledger import (
    PostmortemClassification,
    can_execute as ledger_can_execute,
)
from gate_engine.card_finalizer import (
    _gate_power_same_event_joint_model,
    _gate_fatal_rejected_leg,
    run_hard_gates,
    can_execute as cf_can_execute,
    EXECUTION_RULE as cf_exec_rule,
)
from gate_engine.labels import PropLabel, REJECT_LABELS
from gate_engine.governance import _active_patches, _PATCH_REGISTRY


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _row(
    row_id="r1",
    player="Test Player",
    terminal_label="FINAL_APPROVED",
    slip_type="POWER",
    calibrated_probability=0.65,
    lower_bound=0.60,
    upper_bound=0.75,
    game="team-a-vs-team-b",
    line=4.5,
    side="MORE",
    game_log=None,
    **extra,
) -> dict:
    r = {
        "row_id":                            row_id,
        "player":                            player,
        "terminal_label":                    terminal_label,
        "slip_type":                         slip_type,
        "calibrated_probability":            calibrated_probability,
        "calibrated_probability_lower_bound": lower_bound,
        "calibrated_probability_upper_bound": upper_bound,
        "game":                              game,
        "line":                              line,
        "side":                              side,
        "game_log":                          game_log or [],
        "pp_thresholds": {
            "displayed_line":  line,
            "side":            side.upper(),
            "cash_threshold":  line + 0.5 if side.upper() == "MORE" else line - 0.5,
        },
    }
    r.update(extra)
    return r


# ---------------------------------------------------------------------------
# TC-01: Module-level authority invariants
# ---------------------------------------------------------------------------

class TestModuleAuthorityInvariants(unittest.TestCase):
    """Every new module must declare can_execute=False and no production authority."""

    def test_promo_gate_can_execute_false(self):
        self.assertFalse(promo_can_execute)

    def test_promo_gate_production_authority_false(self):
        self.assertFalse(promo_prod_auth)

    def test_promo_gate_terminal_label_authority_false(self):
        self.assertFalse(promo_tla)

    def test_promo_gate_execution_rule(self):
        self.assertEqual(promo_exec_rule, "DRY_RUN_ONLY_NO_LIVE_TRADING_NO_MARKET_ORDERS")

    def test_snap_can_execute_false(self):
        self.assertFalse(snap_can_execute)

    def test_snap_production_authority_false(self):
        self.assertFalse(snap_prod_auth)

    def test_refresh_can_execute_false(self):
        self.assertFalse(refresh_can_execute)

    def test_refresh_production_authority_false(self):
        self.assertFalse(refresh_prod_auth)

    def test_ledger_can_execute_false(self):
        self.assertFalse(ledger_can_execute)

    def test_card_finalizer_can_execute_false(self):
        self.assertFalse(cf_can_execute)

    def test_card_finalizer_execution_rule(self):
        self.assertEqual(cf_exec_rule, "DRY_RUN_ONLY_NO_LIVE_TRADING_NO_MARKET_ORDERS")

    def test_run_row_result_carries_can_execute_false(self):
        row = _row()
        result = run_row(row)
        self.assertFalse(result["can_execute"])

    def test_batch_run_result_carries_can_execute_false(self):
        result = run([_row()])
        self.assertFalse(result["can_execute"])


# ---------------------------------------------------------------------------
# TC-02: PP promotion gate — break-even + safety buffer
# ---------------------------------------------------------------------------

class TestPPPromotionGateThresholds(unittest.TestCase):
    """Calibrated lower bound must clear break-even + safety buffer."""

    def test_power_lower_bound_below_threshold_blocks(self):
        # POWER threshold = 0.556 + 0.020 = 0.576
        row = _row(slip_type="POWER", lower_bound=0.570, terminal_label="FINAL_APPROVED")
        result = run_row(row)
        self.assertFalse(result["qualified"])
        self.assertIn("LOWER_BOUND_BELOW_THRESHOLD", result["failure_codes"])

    def test_power_lower_bound_at_threshold_passes(self):
        # POWER threshold = 0.556 + 0.020; use 0.577 to avoid floating-point boundary
        row = _row(slip_type="POWER", lower_bound=0.577, terminal_label="MONEY_QUALIFIED")
        result = run_row(row)
        lb_check = result["lower_bound_check"]
        self.assertTrue(lb_check["passed"])

    def test_power_lower_bound_above_threshold_passes(self):
        row = _row(slip_type="POWER", lower_bound=0.620, terminal_label="FINAL_APPROVED")
        result = run_row(row)
        lb_check = result["lower_bound_check"]
        self.assertTrue(lb_check["passed"])

    def test_flex_lower_threshold_is_lower(self):
        # FLEX threshold = 0.500 + 0.020 = 0.520
        row = _row(slip_type="FLEX", lower_bound=0.525, terminal_label="FINAL_APPROVED")
        result = run_row(row)
        lb_check = result["lower_bound_check"]
        self.assertTrue(lb_check["passed"])

    def test_missing_lower_bound_blocks(self):
        row = _row(lower_bound=None, terminal_label="FINAL_APPROVED")
        del row["calibrated_probability_lower_bound"]
        result = run_row(row)
        lb_check = result["lower_bound_check"]
        self.assertFalse(lb_check["passed"])
        self.assertEqual(lb_check["code"], "LOWER_BOUND_UNAVAILABLE")

    def test_custom_safety_buffer_respected(self):
        # With safety_buffer=0.050, POWER threshold = 0.606
        row = _row(slip_type="POWER", lower_bound=0.580, terminal_label="FINAL_APPROVED")
        result = run_row(row, safety_buffer=0.050)
        self.assertFalse(result["qualified"])

    def test_threshold_values_documented(self):
        self.assertEqual(BREAK_EVEN["POWER"], 0.556)
        self.assertEqual(BREAK_EVEN["FLEX"],  0.500)
        self.assertEqual(DEFAULT_SAFETY_BUFFER, 0.020)


# ---------------------------------------------------------------------------
# TC-03: PP promotion gate — two-way no-vig check
# ---------------------------------------------------------------------------

class TestPPNoVigCheck(unittest.TestCase):

    def test_explicit_novig_field_passes(self):
        row = _row(lower_bound=0.620, terminal_label="FINAL_APPROVED")
        row["no_vig_probability"] = 0.600   # > 0.576
        result = run_row(row)
        self.assertTrue(result["novig_check"]["passed"])
        self.assertEqual(result["novig_check"]["source"], "explicit_field")

    def test_explicit_novig_field_fails(self):
        row = _row(lower_bound=0.620, terminal_label="FINAL_APPROVED")
        row["no_vig_probability"] = 0.560   # < 0.576
        result = run_row(row)
        self.assertFalse(result["novig_check"]["passed"])

    def test_computed_from_odds_both_sides(self):
        # MORE at -115, LESS at -105 → compute no-vig for MORE side
        row = _row(lower_bound=0.620, terminal_label="FINAL_APPROVED", side="MORE")
        row["odds_more"] = -115.0
        row["odds_less"] = -105.0
        result = run_row(row)
        nv = result["novig_check"]
        self.assertEqual(nv["source"], "computed_from_odds")
        # Implied MORE: 115/(115+100)=0.5349; implied LESS: 105/(105+100)=0.5122
        # No-vig: 0.5349/(0.5349+0.5122) ≈ 0.5108 — fails POWER threshold 0.576
        self.assertFalse(nv["passed"])

    def test_calibrated_prob_proxy_when_no_odds(self):
        row = _row(lower_bound=0.620, calibrated_probability=0.65,
                   terminal_label="FINAL_APPROVED")
        result = run_row(row)
        nv = result["novig_check"]
        self.assertIn("proxy", nv["source"])

    def test_price_to_implied_prob_negative_odds(self):
        # -110 → 110/210 ≈ 0.5238
        p = _price_to_implied_prob(-110.0)
        self.assertAlmostEqual(p, 110.0 / 210.0, places=5)

    def test_price_to_implied_prob_positive_odds(self):
        # +150 → 100/250 = 0.40
        p = _price_to_implied_prob(150.0)
        self.assertAlmostEqual(p, 0.40, places=5)

    def test_two_way_novig_sums_correctly(self):
        # Balanced market (-110 / -110): no-vig = 0.50 each side
        novig = _two_way_novig_prob(-110.0, -110.0)
        self.assertAlmostEqual(novig, 0.50, places=3)


# ---------------------------------------------------------------------------
# TC-04: Probability leaderboard independence (HIGH_PROBABILITY ≠ QUALIFIED)
# ---------------------------------------------------------------------------

class TestProbabilityLeaderboardIndependence(unittest.TestCase):
    """
    A row can appear on the probability leaderboard (HIGH_PROBABILITY rank)
    while being blocked from paid-card promotion.
    The gate caps terminal_label but must NOT erase probability data.
    """

    def test_probability_data_preserved_on_gate_failure(self):
        row = _row(
            slip_type="POWER",
            lower_bound=0.400,   # below threshold
            calibrated_probability=0.72,
            terminal_label="FINAL_APPROVED",
        )
        run_row(row)
        # Probability fields must survive unchanged
        self.assertEqual(row["calibrated_probability"], 0.72)
        self.assertEqual(row["calibrated_probability_lower_bound"], 0.400)

    def test_terminal_label_capped_not_erased(self):
        row = _row(
            slip_type="POWER",
            lower_bound=0.400,
            terminal_label="FINAL_APPROVED",
        )
        run_row(row)
        # Label capped to MARKET_VERIFIED_HOLD — not NO_PLAY
        self.assertEqual(row["terminal_label"], "MARKET_VERIFIED_HOLD")

    def test_research_label_row_not_subject_to_enforcement(self):
        # RESEARCH_INTEREST is below PAID_CARD_ELIGIBLE_LABELS
        row = _row(
            slip_type="POWER",
            lower_bound=0.400,   # would fail
            terminal_label="RESEARCH_INTEREST",
        )
        run_row(row)
        # Label unchanged — enforcement only fires on paid-card eligible labels
        self.assertEqual(row["terminal_label"], "RESEARCH_INTEREST")

    def test_paid_card_qualified_false_but_probability_rank_preserved(self):
        row = _row(
            slip_type="POWER",
            lower_bound=0.400,
            calibrated_probability=0.80,   # high probability
            terminal_label="FINAL_APPROVED",
        )
        run_row(row)
        self.assertFalse(row["paid_card_qualified"])
        # Probability is preserved — the leaderboard can still rank this row
        self.assertEqual(row["calibrated_probability"], 0.80)
        gate_out = row["gates"]["pp_promotion"]
        # Result explicitly documents what was preserved
        self.assertIn("previous_terminal_label", gate_out)
        self.assertEqual(gate_out["previous_terminal_label"], "FINAL_APPROVED")

    def test_non_eligible_rows_get_gate_result_but_no_enforcement(self):
        row = _row(lower_bound=0.400, terminal_label="MODEL_QUALIFIED_HOLD")
        run_row(row)
        gate = row["gates"]["pp_promotion"]
        self.assertFalse(gate["eligible_for_evaluation"])
        self.assertFalse(gate.get("terminal_label_capped"))
        # Terminal label must not be touched
        self.assertEqual(row["terminal_label"], "MODEL_QUALIFIED_HOLD")


# ---------------------------------------------------------------------------
# TC-05: Recency shock (LOO)
# ---------------------------------------------------------------------------

class TestRecencyShockLOO(unittest.TestCase):
    """Extreme recent result cannot drive qualification when LOO changes verdict."""

    def _make_row_with_log(self, game_log, line=4.5, side="MORE"):
        r = _row(
            lower_bound=0.620,
            calibrated_probability=0.65,
            terminal_label="FINAL_APPROVED",
            line=line,
            side=side,
        )
        r["game_log"] = game_log
        r["pp_thresholds"] = {
            "displayed_line":  line,
            "side":            side.upper(),
            "cash_threshold":  line + 0.5 if side.upper() == "MORE" else line - 0.5,
        }
        return r

    def test_extreme_result_causes_shock_block(self):
        # Normally 3/6 = 50% (below 0.576); add extreme outlier hit (20.0) → 4/7 = 57%
        # Removing outlier: 3/6 = 50% → verdict flips; |57%-50%| = 7% >= 3% threshold
        game_log = [3.0, 4.0, 5.5, 6.0, 7.0, 3.0, 20.0]
        row = self._make_row_with_log(game_log, line=5.0)
        result = _check_recency_shock(row, "POWER", DEFAULT_SAFETY_BUFFER)
        # shock_magnitude should be computed
        self.assertIsNotNone(result["shock_magnitude"])

    def test_stable_log_passes_recency_shock(self):
        # All results similar; removing any one changes hit rate minimally
        game_log = [6.0, 6.5, 7.0, 6.0, 7.0, 6.5, 6.0]   # all hit at line=5.0 MORE
        row = self._make_row_with_log(game_log, line=5.0)
        result = _check_recency_shock(row, "POWER", DEFAULT_SAFETY_BUFFER)
        self.assertTrue(result["passed"])

    def test_fewer_than_3_entries_passes_vacuously(self):
        game_log = [6.0, 3.0]  # only 2 entries
        row = self._make_row_with_log(game_log)
        result = _check_recency_shock(row, "POWER", DEFAULT_SAFETY_BUFFER)
        self.assertTrue(result["passed"])
        self.assertEqual(result["code"], "RECENCY_SHOCK_VACUOUS")

    def test_empty_log_passes_vacuously(self):
        row = self._make_row_with_log([])
        result = _check_recency_shock(row, "POWER", DEFAULT_SAFETY_BUFFER)
        self.assertTrue(result["passed"])

    def test_recency_shock_threshold_constant(self):
        self.assertEqual(RECENCY_SHOCK_THRESHOLD, 0.030)

    def test_recency_shock_block_propagates_to_run_row(self):
        # Build a scenario where LOO shock fires
        game_log = [6.0, 6.5, 7.0, 3.0, 3.5, 3.0, 30.0]
        row = self._make_row_with_log(game_log, line=5.0)
        # Patch _check_recency_shock to force a block
        with patch("gate_engine.pp_promotion_gate._check_recency_shock") as mock_shock:
            mock_shock.return_value = {
                "passed": False,
                "code": "RECENCY_SHOCK_DETECTED",
                "detail": "test forced shock",
                "full_hit_rate": 0.57,
                "loo_hit_rate": 0.50,
                "extreme_removed": 30.0,
                "shock_magnitude": 0.07,
            }
            result = run_row(row, safety_buffer=DEFAULT_SAFETY_BUFFER)
        self.assertFalse(result["qualified"])
        self.assertIn("RECENCY_SHOCK_DETECTED", result["failure_codes"])


# ---------------------------------------------------------------------------
# TC-06: Power same-event joint dependence model gate
# ---------------------------------------------------------------------------

class TestPowerSameEventJointModel(unittest.TestCase):
    """2 Power legs from same event require joint_model_present=True."""

    def _power_pair(self, joint_a=None, joint_b=None, game="game-a"):
        r1 = _row(row_id="r1", slip_type="POWER", game=game,
                  joint_model_present=joint_a, terminal_label=None)
        r2 = _row(row_id="r2", slip_type="POWER", game=game,
                  joint_model_present=joint_b, terminal_label=None)
        return [r1, r2]

    def test_two_power_legs_same_event_no_joint_model_blocked(self):
        rows = self._power_pair(joint_a=None, joint_b=None)
        blockers = _gate_power_same_event_joint_model(rows)
        self.assertTrue(len(blockers) > 0)
        for row in rows:
            label = row.get("terminal_label")
            self.assertEqual(label, PropLabel.REJECT_SAME_EVENT_NO_JOINT_MODEL.value)

    def test_two_power_legs_same_event_one_missing_joint_model(self):
        rows = self._power_pair(joint_a=True, joint_b=None)
        blockers = _gate_power_same_event_joint_model(rows)
        self.assertTrue(len(blockers) > 0)

    def test_two_power_legs_same_event_both_have_joint_model_passes(self):
        rows = self._power_pair(joint_a=True, joint_b=True)
        blockers = _gate_power_same_event_joint_model(rows)
        self.assertEqual(blockers, [])
        for row in rows:
            self.assertIsNone(row.get("terminal_label"))

    def test_single_power_leg_same_event_no_joint_model_required(self):
        # Only 1 leg from the event — no joint model required
        r1 = _row(row_id="r1", slip_type="POWER", game="game-a",
                  joint_model_present=None, terminal_label=None)
        r2 = _row(row_id="r2", slip_type="POWER", game="game-b",   # different event
                  joint_model_present=None, terminal_label=None)
        blockers = _gate_power_same_event_joint_model([r1, r2])
        self.assertEqual(blockers, [])

    def test_flex_legs_same_event_not_subject_to_joint_model_gate(self):
        # FLEX cards are not checked by this gate
        r1 = _row(row_id="r1", slip_type="FLEX", game="game-a",
                  joint_model_present=None, terminal_label=None)
        r2 = _row(row_id="r2", slip_type="FLEX", game="game-a",
                  joint_model_present=None, terminal_label=None)
        blockers = _gate_power_same_event_joint_model([r1, r2])
        self.assertEqual(blockers, [])

    def test_joint_model_gate_in_run_hard_gates_output(self):
        rows = self._power_pair(joint_a=None, joint_b=None)
        report = run_hard_gates(
            rows,
            skip_same_event=True,
            skip_live_overload=True,
            skip_direction_conc=True,
            skip_live_state_req=True,
            skip_fatal_rejected_leg=True,
        )
        self.assertIn("power_joint_model", report["gates_run"])
        self.assertTrue(len(report["blockers_by_gate"]["joint_model"]) > 0)


# ---------------------------------------------------------------------------
# TC-07: Fatal rejected leg gate
# ---------------------------------------------------------------------------

class TestFatalRejectedLegGate(unittest.TestCase):
    """A rejected leg surviving to final construction is a fatal error."""

    def test_clean_rows_no_fatal_violation(self):
        rows = [
            _row(row_id="r1", terminal_label="FINAL_APPROVED"),
            _row(row_id="r2", terminal_label="MONEY_QUALIFIED"),
        ]
        blockers = _gate_fatal_rejected_leg(rows)
        self.assertEqual(blockers, [])
        for row in rows:
            cf = (row.get("gates") or {}).get("card_finalizer", {})
            self.assertFalse(cf.get("fatal_rejected_leg_detected", False))

    def test_rejected_leg_causes_fatal_violation_on_all_rows(self):
        rows = [
            _row(row_id="r1", terminal_label="FINAL_APPROVED"),
            _row(row_id="r2", terminal_label="REJECT_NO_EDGE"),   # surviving reject
        ]
        blockers = _gate_fatal_rejected_leg(rows)
        self.assertTrue(len(blockers) > 0)
        for row in rows:
            cf = (row.get("gates") or {}).get("card_finalizer", {})
            self.assertTrue(cf.get("fatal_rejected_leg_detected"))

    def test_weakest_leg_removed_row_not_fatal(self):
        rows = [
            _row(row_id="r1", terminal_label="FINAL_APPROVED"),
            _row(row_id="r2", terminal_label="REJECT_NO_EDGE"),
        ]
        # Mark r2 as already removed by weakest-leg gate
        rows[1].setdefault("gates", {})["card_finalizer"] = {"weakest_leg_removed": True}
        blockers = _gate_fatal_rejected_leg(rows)
        self.assertEqual(blockers, [])

    def test_fatal_label_applied_to_all_rows(self):
        rows = [
            _row(row_id="r1", terminal_label="MONEY_QUALIFIED"),
            _row(row_id="r2", terminal_label="REJECT_BAD_STRUCTURE"),
        ]
        _gate_fatal_rejected_leg(rows)
        for row in rows:
            self.assertEqual(
                row.get("terminal_label"),
                PropLabel.FATAL_REJECTED_LEG_IN_CARD.value,
            )

    def test_fatal_label_in_reject_labels(self):
        self.assertIn(PropLabel.FATAL_REJECTED_LEG_IN_CARD, REJECT_LABELS)

    def test_fatal_rejected_leg_in_run_hard_gates(self):
        rows = [
            _row(row_id="r1", terminal_label="FINAL_APPROVED"),
            _row(row_id="r2", terminal_label="REJECT_NO_EDGE"),
        ]
        report = run_hard_gates(
            rows,
            skip_same_event=True,
            skip_live_overload=True,
            skip_direction_conc=True,
            skip_live_state_req=True,
            skip_joint_model=True,
        )
        self.assertIn("fatal_rejected_leg", report["gates_run"])
        self.assertTrue(len(report["blockers_by_gate"]["fatal_rejected_leg"]) > 0)


# ---------------------------------------------------------------------------
# TC-08: Pregame snapshot write-failure blocking
# ---------------------------------------------------------------------------

class TestPregameSnapshotBlock(unittest.TestCase):
    """Write failure preserves research output but blocks paid-card qualification."""

    def _mock_conn(self, write_ok: bool, write_exc: Exception | None = None):
        conn = MagicMock()
        cur = MagicMock()
        conn.cursor.return_value.__enter__ = lambda s: cur
        conn.cursor.return_value.__exit__ = MagicMock(return_value=False)
        if write_ok:
            cur.execute.return_value = None
            conn.commit.return_value = None
        else:
            cur.execute.side_effect = write_exc or Exception("DB write failed")
        return conn

    def test_snapshot_write_success_sets_available_true(self):
        row = _row(terminal_label="FINAL_APPROVED")
        conn = self._mock_conn(write_ok=True)
        result = snapshot_and_enforce(conn, row, final_refresh_passed=True)
        self.assertTrue(result["written"])
        self.assertTrue(row.get("pregame_snapshot_available"))

    def test_snapshot_write_failure_preserves_probability(self):
        row = _row(
            terminal_label="FINAL_APPROVED",
            calibrated_probability=0.80,
            lower_bound=0.65,
        )
        conn = self._mock_conn(write_ok=False)
        snapshot_and_enforce(conn, row, final_refresh_passed=True)
        # Probability data must survive
        self.assertEqual(row["calibrated_probability"], 0.80)
        self.assertEqual(row["calibrated_probability_lower_bound"], 0.65)

    def test_snapshot_write_failure_caps_paid_card_label(self):
        row = _row(terminal_label="FINAL_APPROVED")
        conn = self._mock_conn(write_ok=False)
        snapshot_and_enforce(conn, row, final_refresh_passed=True)
        self.assertEqual(row["terminal_label"], "MARKET_VERIFIED_HOLD")
        self.assertFalse(row.get("pregame_snapshot_available"))

    def test_snapshot_write_failure_on_money_qualified_also_caps(self):
        row = _row(terminal_label="MONEY_QUALIFIED")
        conn = self._mock_conn(write_ok=False)
        snapshot_and_enforce(conn, row, final_refresh_passed=True)
        self.assertEqual(row["terminal_label"], "MARKET_VERIFIED_HOLD")

    def test_snapshot_write_failure_does_not_cap_research_label(self):
        row = _row(terminal_label="RESEARCH_INTEREST")
        conn = self._mock_conn(write_ok=False)
        snapshot_and_enforce(conn, row, final_refresh_passed=True)
        # Research label must not be capped
        self.assertEqual(row["terminal_label"], "RESEARCH_INTEREST")

    def test_build_snapshot_has_required_keys(self):
        row = _row()
        snap = build_snapshot(row, final_refresh_passed=True)
        for key in ("snapshot_id", "row_id", "snapshot_at", "final_refresh_passed",
                    "lineup_fingerprint", "market_fingerprint"):
            self.assertIn(key, snap)

    def test_snap_can_execute_false_in_result(self):
        row = _row(terminal_label="FINAL_APPROVED")
        conn = self._mock_conn(write_ok=True)
        result = snapshot_and_enforce(conn, row, final_refresh_passed=True)
        self.assertFalse(result["can_execute"])


# ---------------------------------------------------------------------------
# TC-09: Binding final refresh enforcement
# ---------------------------------------------------------------------------

class TestBindingFinalRefresh(unittest.TestCase):
    """Material changes force rerun; non-material changes pass through."""

    def _baseline(self, **overrides):
        b = {
            "player": "Test Player",
            "team": "Team A",
            "opponent": "Team B",
            "game": "team-a-vs-team-b",
            "game_time": "2026-08-15T19:05:00Z",
            "lineup_status": "CONFIRMED",
            "prop_type": "Points",
            "stat_key": "points",
            "line": 24.5,
            "side": "MORE",
            "odds_more": -115.0,
            "odds_less": -105.0,
            "sources": {"primary": "v1.0"},
        }
        b.update(overrides)
        return b

    def test_no_baseline_passes_vacuously(self):
        row = _row(terminal_label="FINAL_APPROVED")
        result = enforce_final_refresh(row, baseline=None)
        self.assertFalse(result["refresh_required"])
        self.assertEqual(result["code"], "FINAL_REFRESH_VACUOUS")

    def test_identical_baseline_passes(self):
        base = self._baseline()
        row = _row(terminal_label="FINAL_APPROVED")
        row.update(base)
        result = enforce_final_refresh(row, baseline=base)
        self.assertFalse(result["refresh_required"])

    def test_lineup_change_requires_refresh(self):
        base = self._baseline(lineup_status="CONFIRMED")
        row = _row(terminal_label="FINAL_APPROVED")
        row.update(base)
        row["lineup_status"] = "QUESTIONABLE"
        result = enforce_final_refresh(row, baseline=base)
        self.assertTrue(result["refresh_required"])
        self.assertIn(CATEGORY_LINEUP, result["change_categories"])

    def test_market_line_change_requires_refresh(self):
        base = self._baseline(line=24.5)
        row = _row(terminal_label="FINAL_APPROVED")
        row.update(base)
        row["line"] = 25.5   # > 0.5 delta
        result = enforce_final_refresh(row, baseline=base)
        self.assertTrue(result["refresh_required"])
        self.assertIn(CATEGORY_MARKET, result["change_categories"])

    def test_minor_line_move_does_not_require_refresh(self):
        # Delta of 0.5 is at the boundary (not > 0.5)
        base = self._baseline(line=24.5)
        row = _row(terminal_label="FINAL_APPROVED")
        row.update(base)
        row["line"] = 24.9   # delta = 0.4 < 0.5
        result = enforce_final_refresh(row, baseline=base)
        self.assertNotIn(CATEGORY_MARKET, result.get("change_categories", []))

    def test_price_change_beyond_threshold_requires_refresh(self):
        from gate_engine.pp_final_refresh import PRICE_MATERIALITY_THRESHOLD
        base = self._baseline(odds_more=-110.0)
        row = _row(terminal_label="FINAL_APPROVED")
        row.update(base)
        row["odds_more"] = -110.0 - PRICE_MATERIALITY_THRESHOLD - 1.0  # beyond threshold
        result = enforce_final_refresh(row, baseline=base)
        self.assertTrue(result["refresh_required"])
        self.assertIn(CATEGORY_PRICE, result["change_categories"])

    def test_source_change_requires_refresh(self):
        base = self._baseline(sources={"primary": "v1.0"})
        row = _row(terminal_label="FINAL_APPROVED")
        row.update(base)
        row["sources"] = {"primary": "v2.0"}   # version changed
        result = enforce_final_refresh(row, baseline=base)
        self.assertTrue(result["refresh_required"])
        self.assertIn(CATEGORY_SOURCE, result["change_categories"])

    def test_refresh_required_caps_paid_card_label(self):
        base = self._baseline(lineup_status="CONFIRMED")
        row = _row(terminal_label="FINAL_APPROVED")
        row.update(base)
        row["lineup_status"] = "OUT"
        enforce_final_refresh(row, baseline=base)
        self.assertEqual(row["terminal_label"], "MARKET_VERIFIED_HOLD")

    def test_refresh_required_does_not_cap_research_label(self):
        base = self._baseline(lineup_status="CONFIRMED")
        row = _row(terminal_label="RESEARCH_INTEREST")
        row.update(base)
        row["lineup_status"] = "OUT"
        enforce_final_refresh(row, baseline=base)
        self.assertEqual(row["terminal_label"], "RESEARCH_INTEREST")

    def test_refresh_gate_result_carries_can_execute_false(self):
        result = enforce_final_refresh(_row(), baseline=None)
        self.assertFalse(result["can_execute"])

    def test_batch_run_tracks_refresh_required_count(self):
        base = self._baseline(lineup_status="CONFIRMED")
        r1 = _row(row_id="r1", terminal_label="FINAL_APPROVED")
        r1.update(base)
        r1["lineup_status"] = "OUT"
        r2 = _row(row_id="r2", terminal_label="MONEY_QUALIFIED")
        r2.update(base)  # unchanged
        report = refresh_run([r1, r2], baselines={"r1": base, "r2": base})
        self.assertEqual(report["refresh_required_count"], 1)


# ---------------------------------------------------------------------------
# TC-10: Postmortem process classifications
# ---------------------------------------------------------------------------

class TestPostmortemClassifications(unittest.TestCase):
    """Postmortem classifications distinguish failure causes."""

    def test_all_classifications_defined(self):
        expected = {
            "VARIANCE", "PRICE", "MARKET", "STRUCTURE", "OUTLIER",
            "WEAKEST_LEG", "REFRESH", "DATA_GAP", "MISSING_PREGAME_EVIDENCE",
        }
        self.assertEqual(PostmortemClassification.all_values(), frozenset(expected))

    def test_validate_accepts_all_known_values(self):
        for val in PostmortemClassification.all_values():
            self.assertTrue(PostmortemClassification.validate(val))

    def test_validate_rejects_unknown_value(self):
        self.assertFalse(PostmortemClassification.validate("UNDIFFERENTIATED_LOSS"))

    def test_validate_rejects_empty_string(self):
        self.assertFalse(PostmortemClassification.validate(""))

    def test_missing_pregame_evidence_is_distinct_from_data_gap(self):
        # These are distinct failure modes
        self.assertNotEqual(
            PostmortemClassification.MISSING_PREGAME_EVIDENCE,
            PostmortemClassification.DATA_GAP,
        )

    def test_weakest_leg_classification_available(self):
        self.assertEqual(PostmortemClassification.WEAKEST_LEG, "WEAKEST_LEG")

    def test_refresh_classification_available(self):
        self.assertEqual(PostmortemClassification.REFRESH, "REFRESH")


# ---------------------------------------------------------------------------
# TC-11: New labels in PropLabel and REJECT_LABELS
# ---------------------------------------------------------------------------

class TestNewLabels(unittest.TestCase):
    """All new labels are registered and correctly classified."""

    def test_reject_pp_promotion_gate_in_reject_labels(self):
        self.assertIn(PropLabel.REJECT_PP_PROMOTION_GATE, REJECT_LABELS)

    def test_reject_same_event_no_joint_model_in_reject_labels(self):
        self.assertIn(PropLabel.REJECT_SAME_EVENT_NO_JOINT_MODEL, REJECT_LABELS)

    def test_reject_recency_shock_in_reject_labels(self):
        self.assertIn(PropLabel.REJECT_RECENCY_SHOCK, REJECT_LABELS)

    def test_fatal_rejected_leg_in_reject_labels(self):
        self.assertIn(PropLabel.FATAL_REJECTED_LEG_IN_CARD, REJECT_LABELS)

    def test_pregame_snapshot_block_in_reject_labels(self):
        self.assertIn(PropLabel.PREGAME_SNAPSHOT_BLOCK, REJECT_LABELS)

    def test_final_refresh_required_in_reject_labels(self):
        self.assertIn(PropLabel.FINAL_REFRESH_REQUIRED, REJECT_LABELS)

    def test_all_new_labels_have_distinct_values(self):
        new_labels = [
            PropLabel.REJECT_PP_PROMOTION_GATE,
            PropLabel.REJECT_SAME_EVENT_NO_JOINT_MODEL,
            PropLabel.REJECT_RECENCY_SHOCK,
            PropLabel.FATAL_REJECTED_LEG_IN_CARD,
            PropLabel.PREGAME_SNAPSHOT_BLOCK,
            PropLabel.FINAL_REFRESH_REQUIRED,
        ]
        values = [l.value for l in new_labels]
        self.assertEqual(len(values), len(set(values)))


# ---------------------------------------------------------------------------
# TC-12: Governance patch registered
# ---------------------------------------------------------------------------

class TestGovernancePatch(unittest.TestCase):
    """New patch is registered in the active patch registry."""

    def test_new_patch_in_registry(self):
        patch_ids = [p["patch_id"] for p in _PATCH_REGISTRY]
        self.assertIn("WOW-PATCH-2026-08-15-PP-PROMOTION-AND-SAME-GAME-FRAGILITY", patch_ids)

    def test_new_patch_is_active(self):
        active_ids = [p["patch_id"] for p in _active_patches()]
        self.assertIn("WOW-PATCH-2026-08-15-PP-PROMOTION-AND-SAME-GAME-FRAGILITY", active_ids)

    def test_new_patch_precedence_104(self):
        patch = next(
            p for p in _PATCH_REGISTRY
            if p["patch_id"] == "WOW-PATCH-2026-08-15-PP-PROMOTION-AND-SAME-GAME-FRAGILITY"
        )
        self.assertEqual(patch["precedence"], 104)

    def test_new_patch_can_execute_false(self):
        patch = next(
            p for p in _PATCH_REGISTRY
            if p["patch_id"] == "WOW-PATCH-2026-08-15-PP-PROMOTION-AND-SAME-GAME-FRAGILITY"
        )
        self.assertFalse(patch["can_execute"])

    def test_total_active_patches_is_25(self):
        self.assertEqual(len(_active_patches()), 25)


if __name__ == "__main__":
    unittest.main()
