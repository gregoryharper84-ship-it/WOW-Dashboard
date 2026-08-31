"""
gate_engine/tests/test_full_model_gatekeeper.py
WOW Full Model Contract Gatekeeper v1.1 — Regression Suite

Covers all 11 mandatory regression scenarios from the spec:
  T01  Missing or role-mismatched L10 cannot silently pass where required
  T02  Adjacent-line no-vig cannot satisfy exact-line no-vig
  T03  Point probability cannot masquerade as calibrated lower bound
  T04  Unresolved push rules block relevant payout qualification
  T05  Upstream HOLD/REJECT ceilings cannot be upgraded
  T06  Late critical participant/status change invalidates the prior pass
  T07  Started events cannot survive final refresh (event_started flag)
  T08  Unresolved slip correlation remains downstream-blocking
  T09  Kalshi Recovery Mode can reject even if every component passes
  T10  Every candidate reconciles to exactly one output state
  T11  No FINAL_APPROVED can survive any supported run path without Gatekeeper PASS

Plus supporting unit tests for individual gate checks.
"""
from __future__ import annotations

import unittest
from copy import deepcopy

from gate_engine import full_model_gatekeeper as fmcg
from gate_engine.full_model_gatekeeper import (
    QUAL_PASS, QUAL_HOLD, QUAL_REJECT,
    STATUS_COMPLETE, STATUS_INCOMPLETE, STATUS_INVALIDATED,
    FINAL_APPROVED, MODEL_QUALIFIED_HOLD,
    GATE_PASS, GATE_FAIL, GATE_HOLD, GATE_SKIP,
    CAN_EXECUTE,
    apply_gatekeeper, apply_gatekeeper_batch, evaluate,
    verify_cc_envelope, verify_v16_result,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _base_gates() -> dict:
    """Minimal gate dicts that allow all completeness checks to pass."""
    return {
        "slate_validation": {"passed": True},
        "status_role": {
            "passed": True,
            "role_status": "CONFIRMED",
            "role_timestamp": "2026-08-14T12:00:00Z",
        },
        "l5_l10_ledger": {
            "passed": True,
            "l10_hit_rate": 0.72,
            "l5_hit_rate": 0.80,
            "line": 2.5,
        },
        "market_gate": {
            "passed": True,
            "market_status": "MARKET_VERIFIED",
            "sportsbook_line": 2.5,
            "no_vig_prob": 0.61,
            "exact_market_no_vig_prob": 0.61,
            "exact_market_line": 2.5,
            "confidence_cap": None,
        },
        "ev_gate": {
            "passed": True,
            "edge_score": 0.04,
        },
        "slip_structure": {"passed": True},
        "exposure_gate": {"passed": True},
    }


def _base_row(**overrides) -> dict:
    """Fully valid row that should produce PASS when no overrides break it."""
    row = {
        "row_id":           "test-row-001",
        "player":           "Test Player",
        "sport":            "MLB",
        "prop_type":        "hits",
        "line":             2.5,
        "terminal_label":   FINAL_APPROVED,
        "blockers":         [],
        "gates":            _base_gates(),
        # Calibrated probability from ACTIVE model
        "calibrated_probability": 0.65,
        "calibrated_probability_lower_bound": None,  # not FS model
        "model_status":     "ACTIVE",
        "model_id":         "mlb_hits_binomial_pa_v2",
        "probability_publishable": True,
        # pp_thresholds (half-point → push not required)
        "pp_thresholds": {
            "whole_number_line": False,
            "cash_threshold": 3.0,
        },
        # No invalidation signals
    }
    row.update(overrides)
    return row


def _apply(row: dict) -> dict:
    """Apply gatekeeper and return the gatekeeper result."""
    apply_gatekeeper(row, governance_hash="test-hash-abc")
    return row["gatekeeper"]


# ---------------------------------------------------------------------------
# T01 — Missing / role-mismatched L10
# ---------------------------------------------------------------------------

class TestT01_L10Evidence(unittest.TestCase):

    def test_missing_l10_gate_blocks_pass(self):
        """Missing l5_l10_ledger gate → INCOMPLETE, FINAL_APPROVED downgraded."""
        row = _base_row()
        del row["gates"]["l5_l10_ledger"]
        gk = _apply(row)
        self.assertEqual(gk["full_model_status"], STATUS_INCOMPLETE)
        self.assertEqual(gk["qualification_result"], QUAL_REJECT)
        self.assertEqual(row["terminal_label"], MODEL_QUALIFIED_HOLD)

    def test_l10_not_passed_blocks(self):
        """l5_l10_ledger with passed=False → REJECT."""
        row = _base_row()
        row["gates"]["l5_l10_ledger"]["passed"] = False
        gk = _apply(row)
        self.assertEqual(gk["qualification_result"], QUAL_REJECT)
        self.assertEqual(row["terminal_label"], MODEL_QUALIFIED_HOLD)
        self.assertTrue(any("L10_EVIDENCE" in b for b in gk["blockers"]))

    def test_role_line_mismatch_blocks(self):
        """L10 ledger line different from scored line → REJECT."""
        row = _base_row()
        row["gates"]["l5_l10_ledger"]["line"] = 1.5  # scored line is 2.5
        gk = _apply(row)
        self.assertEqual(gk["qualification_result"], QUAL_REJECT)
        self.assertEqual(row["terminal_label"], MODEL_QUALIFIED_HOLD)
        self.assertTrue(any("ROLE_LINE_MISMATCH" in b for b in gk["blockers"]))

    def test_l10_sole_qualifier_blocks(self):
        """L10 passes but market_gate does not → sole qualifier → REJECT."""
        row = _base_row()
        row["gates"]["market_gate"]["passed"] = False
        # l10 is still passed
        gk = _apply(row)
        self.assertEqual(gk["qualification_result"], QUAL_REJECT)
        self.assertTrue(any("SOLE_QUALIFIER" in b for b in gk["blockers"]))

    def test_all_three_gate_pass_required_for_l10_pass(self):
        """l10 + market + ev all pass → L10 gate passes (not sole qualifier)."""
        row = _base_row()
        gk = _apply(row)
        gr = gk["gate_results"]["l10_evidence"]
        self.assertEqual(gr["status"], GATE_PASS)


# ---------------------------------------------------------------------------
# T02 — Adjacent-line no-vig cannot satisfy exact-line
# ---------------------------------------------------------------------------

class TestT02_NoVigExactLine(unittest.TestCase):

    def test_adjacent_only_is_hold_not_pass(self):
        """Only no_vig_prob (adjacent) present, exact_market_no_vig_prob=None → HOLD."""
        row = _base_row()
        row["gates"]["market_gate"]["exact_market_no_vig_prob"] = None
        row["gates"]["market_gate"]["adjacent_market_line"] = 2.0
        # no_vig_prob still set
        gk = _apply(row)
        gr = gk["gate_results"]["no_vig_exact_line"]
        self.assertEqual(gr["status"], GATE_HOLD)
        self.assertTrue(any("ADJACENT_LINE_ONLY" in b for b in gk["blockers"]))
        # HOLD propagates to qualification
        self.assertIn(gk["qualification_result"], [QUAL_HOLD, QUAL_REJECT])
        self.assertEqual(row["terminal_label"], MODEL_QUALIFIED_HOLD)

    def test_exact_line_no_vig_present_passes(self):
        """exact_market_no_vig_prob present → gate passes."""
        row = _base_row()
        gk = _apply(row)
        gr = gk["gate_results"]["no_vig_exact_line"]
        self.assertEqual(gr["status"], GATE_PASS)

    def test_missing_all_no_vig_is_fail(self):
        """No no-vig data at all AND sportsbook_line present → FAIL."""
        row = _base_row()
        row["gates"]["market_gate"]["exact_market_no_vig_prob"] = None
        row["gates"]["market_gate"]["no_vig_prob"] = None
        # sportsbook_line still set
        gk = _apply(row)
        gr = gk["gate_results"]["no_vig_exact_line"]
        self.assertEqual(gr["status"], GATE_FAIL)


# ---------------------------------------------------------------------------
# T03 — Point probability cannot masquerade as calibrated lower bound
# ---------------------------------------------------------------------------

class TestT03_CalibratedLowerBound(unittest.TestCase):

    def test_lower_bound_equals_cal_prob_is_rejected(self):
        """lower_bound == calibrated_probability → MASQUERADES_AS_POINT_PROB."""
        row = _base_row()
        row["calibrated_probability"] = 0.65
        row["calibrated_probability_lower_bound"] = 0.65  # same value
        gk = _apply(row)
        gr = gk["gate_results"]["calibrated_lower_bound"]
        self.assertEqual(gr["status"], GATE_FAIL)
        self.assertTrue(any("MASQUERADES_AS_POINT_PROB" in b for b in gk["blockers"]))
        self.assertEqual(row["terminal_label"], MODEL_QUALIFIED_HOLD)

    def test_lower_bound_above_cal_prob_is_rejected(self):
        """lower_bound > calibrated_probability → EXCEEDS_POINT_ESTIMATE."""
        row = _base_row()
        row["calibrated_probability"] = 0.60
        row["calibrated_probability_lower_bound"] = 0.70
        gk = _apply(row)
        gr = gk["gate_results"]["calibrated_lower_bound"]
        self.assertEqual(gr["status"], GATE_FAIL)
        self.assertTrue(any("EXCEEDS_POINT_ESTIMATE" in b for b in gk["blockers"]))

    def test_valid_lower_bound_passes(self):
        """lower_bound < calibrated_probability → PASS."""
        row = _base_row()
        row["calibrated_probability"] = 0.65
        row["calibrated_probability_lower_bound"] = 0.58
        gk = _apply(row)
        gr = gk["gate_results"]["calibrated_lower_bound"]
        self.assertEqual(gr["status"], GATE_PASS)

    def test_absent_lower_bound_skipped_for_non_fs(self):
        """lower_bound absent and non-FS model → SKIP (not a failure)."""
        row = _base_row()
        row["calibrated_probability_lower_bound"] = None
        row["model_id"] = "mlb_hits_binomial_pa_v2"
        gk = _apply(row)
        gr = gk["gate_results"]["calibrated_lower_bound"]
        self.assertEqual(gr["status"], GATE_SKIP)


# ---------------------------------------------------------------------------
# T04 — Unresolved push rules block payout qualification
# ---------------------------------------------------------------------------

class TestT04_PushRules(unittest.TestCase):

    def test_whole_number_line_without_push_prob_is_hold(self):
        """Whole-number line, no push_prob → HOLD (blocks payout qualification)."""
        row = _base_row()
        row["pp_thresholds"] = {"whole_number_line": True, "cash_threshold": 4.0}
        row["push_prob"] = None
        gk = _apply(row)
        gr = gk["gate_results"]["push_rules"]
        self.assertEqual(gr["status"], GATE_HOLD)
        self.assertTrue(any("PUSH_RULES" in b for b in gk["blockers"]))
        self.assertEqual(row["terminal_label"], MODEL_QUALIFIED_HOLD)

    def test_half_point_line_skips_push_check(self):
        """Half-point line → push structurally impossible → SKIP."""
        row = _base_row()
        row["pp_thresholds"] = {"whole_number_line": False, "cash_threshold": 3.0}
        gk = _apply(row)
        gr = gk["gate_results"]["push_rules"]
        self.assertEqual(gr["status"], GATE_SKIP)

    def test_whole_number_with_push_prob_passes(self):
        """Whole-number line with push_prob resolved → PASS."""
        row = _base_row()
        row["pp_thresholds"] = {"whole_number_line": True, "cash_threshold": 4.0}
        row["push_prob"] = 0.08
        gk = _apply(row)
        gr = gk["gate_results"]["push_rules"]
        self.assertEqual(gr["status"], GATE_PASS)


# ---------------------------------------------------------------------------
# T05 — Upstream HOLD/REJECT ceilings cannot be upgraded
# ---------------------------------------------------------------------------

class TestT05_UpstreamCeilingPreserved(unittest.TestCase):

    def test_model_qualified_hold_is_not_upgraded_to_final_approved(self):
        """Entry label MODEL_QUALIFIED_HOLD → gatekeeper does not upgrade it."""
        row = _base_row(terminal_label=MODEL_QUALIFIED_HOLD)
        apply_gatekeeper(row, governance_hash="test-hash")
        # Gatekeeper should leave MODEL_QUALIFIED_HOLD alone (not upgrade to FINAL_APPROVED)
        self.assertNotEqual(row["terminal_label"], FINAL_APPROVED)
        self.assertEqual(row["terminal_label"], MODEL_QUALIFIED_HOLD)

    def test_reject_label_is_not_upgraded(self):
        """Entry label REJECT_NO_EDGE → gatekeeper does not upgrade it."""
        row = _base_row(terminal_label="REJECT_NO_EDGE")
        apply_gatekeeper(row, governance_hash="test-hash")
        self.assertEqual(row["terminal_label"], "REJECT_NO_EDGE")

    def test_slate_purge_is_not_upgraded(self):
        """Entry SLATE_PURGE → gatekeeper leaves it (most restrictive preserved)."""
        row = _base_row(terminal_label="SLATE_PURGE")
        apply_gatekeeper(row, governance_hash="test-hash")
        self.assertEqual(row["terminal_label"], "SLATE_PURGE")

    def test_lowest_ceiling_is_most_restrictive_of_entry_and_proposed(self):
        """lowest_ceiling in gatekeeper result is always >= restrictiveness of entry_label."""
        row = _base_row(terminal_label="REJECT_NO_EDGE")
        gk = _apply(row)
        # Gatekeeper proposed MODEL_QUALIFIED_HOLD but entry is REJECT_NO_EDGE
        # Most restrictive should win
        from gate_engine.full_model_gatekeeper import _ceiling_rank
        self.assertGreaterEqual(
            _ceiling_rank(gk["lowest_ceiling"]),
            _ceiling_rank(MODEL_QUALIFIED_HOLD),
        )


# ---------------------------------------------------------------------------
# T06 — Late critical participant change invalidates prior pass
# ---------------------------------------------------------------------------

class TestT06_InvalidationMaterialStatusChange(unittest.TestCase):

    def test_material_status_change_invalidates(self):
        """material_status_change=True → STATUS_INVALIDATED, FINAL_APPROVED downgraded."""
        row = _base_row(material_status_change=True)
        gk = _apply(row)
        self.assertEqual(gk["full_model_status"], STATUS_INVALIDATED)
        self.assertEqual(gk["qualification_result"], QUAL_REJECT)
        self.assertEqual(row["terminal_label"], MODEL_QUALIFIED_HOLD)
        self.assertTrue(gk["invalidation_state"]["is_invalidated"])
        self.assertTrue(gk["invalidation_state"]["requires_rerun"])

    def test_starter_changed_invalidates(self):
        """starter_changed=True → STATUS_INVALIDATED."""
        row = _base_row(starter_changed=True)
        gk = _apply(row)
        self.assertEqual(gk["full_model_status"], STATUS_INVALIDATED)
        self.assertEqual(row["terminal_label"], MODEL_QUALIFIED_HOLD)

    def test_lineup_change_invalidates(self):
        """lineup_finalized_after_score=True → STATUS_INVALIDATED."""
        row = _base_row(lineup_finalized_after_score=True)
        gk = _apply(row)
        self.assertEqual(gk["full_model_status"], STATUS_INVALIDATED)

    def test_no_invalidation_signals_passes(self):
        """No invalidation signals → STATUS_COMPLETE (not invalidated)."""
        row = _base_row()
        gk = _apply(row)
        self.assertFalse(gk["invalidation_state"]["is_invalidated"])
        self.assertEqual(gk["full_model_status"], STATUS_COMPLETE)


# ---------------------------------------------------------------------------
# T07 — Started events cannot survive final refresh
# ---------------------------------------------------------------------------

class TestT07_EventStarted(unittest.TestCase):

    def test_event_started_invalidates(self):
        """event_started=True → STATUS_INVALIDATED, FINAL_APPROVED downgraded."""
        row = _base_row(event_started=True)
        gk = _apply(row)
        self.assertEqual(gk["full_model_status"], STATUS_INVALIDATED)
        self.assertEqual(row["terminal_label"], MODEL_QUALIFIED_HOLD)
        self.assertIn("EVENT_STARTED", str(gk["blockers"]))

    def test_price_age_exceeded_invalidates(self):
        """price_age_exceeded=True → STATUS_INVALIDATED."""
        row = _base_row(price_age_exceeded=True)
        gk = _apply(row)
        self.assertEqual(gk["full_model_status"], STATUS_INVALIDATED)
        self.assertEqual(row["terminal_label"], MODEL_QUALIFIED_HOLD)

    def test_weather_material_change_invalidates(self):
        """weather_material_change=True → STATUS_INVALIDATED."""
        row = _base_row(weather_material_change=True)
        gk = _apply(row)
        self.assertEqual(gk["full_model_status"], STATUS_INVALIDATED)


# ---------------------------------------------------------------------------
# T08 — Unresolved slip correlation remains downstream-blocking
# ---------------------------------------------------------------------------

class TestT08_SlipCorrelationDownstream(unittest.TestCase):

    def test_gatekeeper_pass_does_not_clear_slip_dependency(self):
        """
        Even when gatekeeper returns PASS, slip_card_dependency remains
        in required_downstream_governors — it is never cleared by this module.
        """
        row = _base_row()
        gk = _apply(row)
        # Verify PASS
        self.assertEqual(gk["qualification_result"], QUAL_PASS)
        # Verify downstream governor is still listed
        self.assertIn("slip_card_dependency", gk["required_downstream_governors"])
        self.assertIn("joint_probability",    gk["required_downstream_governors"])
        self.assertIn("weakest_leg",          gk["required_downstream_governors"])

    def test_gatekeeper_note_states_not_sufficient(self):
        """Gatekeeper PASS note explicitly states it is not sufficient."""
        row = _base_row()
        gk = _apply(row)
        self.assertIn("NOT sufficient", gk["note"])
        self.assertIn("required_downstream_governors", gk["note"])


# ---------------------------------------------------------------------------
# T09 — Kalshi Recovery Mode rejects combination even if components pass
# ---------------------------------------------------------------------------

class TestT09_KalshiRecoveryMode(unittest.TestCase):

    def test_kalshi_recovery_mode_remains_downstream_governor(self):
        """
        kalshi_portfolio_recovery_governor must remain in required_downstream_governors
        for any row, including one where the gatekeeper returns PASS.
        The gatekeeper itself does not apply Kalshi portfolio rules.
        """
        row = _base_row()
        gk = _apply(row)
        self.assertIn("kalshi_portfolio_recovery_governor",
                      gk["required_downstream_governors"])
        # Even on PASS the downstream governor is not cleared
        self.assertEqual(gk["qualification_result"], QUAL_PASS)
        self.assertIn("kalshi_portfolio_recovery_governor",
                      gk["required_downstream_governors"])

    def test_cc_envelope_with_no_gatekeeper_pass_is_downgraded(self):
        """
        CC envelope with engine_label=FINAL_APPROVED but no gatekeeper result
        → downgraded to MODEL_QUALIFIED_HOLD. This simulates a Kalshi component
        that individually passes but whose engine result lacks a gatekeeper pass.
        """
        envelope = {
            "engine_label":  FINAL_APPROVED,
            "engine_result": {},   # no gatekeeper key
            "cc_blockers":   [],
        }
        ok = verify_cc_envelope(envelope)
        self.assertFalse(ok)
        self.assertEqual(envelope["engine_label"], MODEL_QUALIFIED_HOLD)
        self.assertTrue(any("NO_GATEKEEPER_PASS" in b
                            for b in envelope.get("cc_blockers", [])))

    def test_cc_envelope_with_valid_gatekeeper_pass_survives(self):
        """
        CC envelope with engine_label=FINAL_APPROVED and valid gatekeeper PASS
        → engine_label preserved.
        """
        envelope = {
            "engine_label": FINAL_APPROVED,
            "engine_result": {
                "gatekeeper": {
                    "qualification_result": QUAL_PASS,
                    "full_model_status":    STATUS_COMPLETE,
                    "can_execute":          False,
                }
            },
            "cc_blockers": [],
        }
        ok = verify_cc_envelope(envelope)
        self.assertTrue(ok)
        self.assertEqual(envelope["engine_label"], FINAL_APPROVED)


# ---------------------------------------------------------------------------
# T10 — Every candidate reconciles to exactly one output state
# ---------------------------------------------------------------------------

class TestT10_SingleOutputState(unittest.TestCase):

    def test_every_row_gets_gatekeeper_result(self):
        """apply_gatekeeper_batch: every row has a gatekeeper result dict."""
        rows = [_base_row() for _ in range(5)]
        apply_gatekeeper_batch(rows, governance_hash="test-hash")
        for row in rows:
            self.assertIn("gatekeeper", row)
            gk = row["gatekeeper"]
            self.assertIn(gk["full_model_status"],
                          [STATUS_COMPLETE, STATUS_INCOMPLETE, STATUS_INVALIDATED])
            self.assertIn(gk["qualification_result"],
                          [QUAL_PASS, QUAL_HOLD, QUAL_REJECT])

    def test_gatekeeper_result_always_has_can_execute_false(self):
        """can_execute is always False in the gatekeeper result."""
        row = _base_row()
        gk = _apply(row)
        self.assertIs(gk["can_execute"], False)

    def test_summary_counts_sum_to_total(self):
        """Batch summary counts sum to total rows processed."""
        rows = [
            _base_row(),
            _base_row(terminal_label=MODEL_QUALIFIED_HOLD),
            _base_row(event_started=True),
        ]
        summary = apply_gatekeeper_batch(rows, governance_hash="test-hash")
        total = (
            summary["status_complete_pass"]
            + summary["status_complete_hold"]
            + summary["status_complete_reject"]
            + summary["status_invalidated"]
            + summary["status_incomplete"]
        )
        self.assertEqual(total, summary["total_rows"])

    def test_evaluate_returns_all_required_keys(self):
        """evaluate() output always contains every required top-level key."""
        required_keys = {
            "gatekeeper_version", "contract_id", "patch_id", "patch_precedence",
            "engine_version", "governance_hash", "evaluated_at",
            "candidate_id", "full_model_status", "qualification_result",
            "controlling_specialist", "active_qualification_rule",
            "lowest_ceiling", "terminal_label", "entry_terminal_label",
            "can_execute", "dry_run_only",
            "gate_results", "probability_summary", "market_summary",
            "blockers", "invalidation_state", "required_downstream_governors",
        }
        row = _base_row()
        result = evaluate(row, governance_hash="test-hash")
        for k in required_keys:
            self.assertIn(k, result, f"Missing key: {k}")


# ---------------------------------------------------------------------------
# T11 — No FINAL_APPROVED without valid Gatekeeper PASS
# ---------------------------------------------------------------------------

class TestT11_NoFinalApprovedWithoutPass(unittest.TestCase):

    def test_invalid_row_cannot_retain_final_approved(self):
        """Row with missing gates cannot retain FINAL_APPROVED."""
        row = _base_row()
        del row["gates"]["market_gate"]
        apply_gatekeeper(row, governance_hash="test-hash")
        self.assertNotEqual(row["terminal_label"], FINAL_APPROVED)

    def test_provisional_model_cannot_reach_final_approved(self):
        """PROVISIONAL model status → HOLD → FINAL_APPROVED downgraded."""
        row = _base_row()
        row["model_status"] = "PROVISIONAL"
        gk = _apply(row)
        self.assertNotEqual(row["terminal_label"], FINAL_APPROVED)
        self.assertIn(gk["qualification_result"], [QUAL_HOLD, QUAL_REJECT])

    def test_no_registered_model_cannot_reach_final_approved(self):
        """NO_REGISTERED_MODEL → REJECT → FINAL_APPROVED downgraded."""
        row = _base_row()
        row["model_status"] = "NO_REGISTERED_MODEL"
        gk = _apply(row)
        self.assertEqual(row["terminal_label"], MODEL_QUALIFIED_HOLD)
        self.assertEqual(gk["qualification_result"], QUAL_REJECT)

    def test_source_conflict_blocks_final_approved(self):
        """SOURCE_CONFLICT in blockers → contradiction audit FAIL → downgrade."""
        row = _base_row()
        row["blockers"] = ["SOURCE_CONFLICT:foo"]
        gk = _apply(row)
        self.assertEqual(row["terminal_label"], MODEL_QUALIFIED_HOLD)
        self.assertTrue(any("CONTRADICTION_AUDIT" in b for b in gk["blockers"]))

    def test_valid_full_model_allows_final_approved(self):
        """A fully valid row with ACTIVE model retains FINAL_APPROVED."""
        row = _base_row()
        gk = _apply(row)
        self.assertEqual(gk["qualification_result"], QUAL_PASS)
        self.assertEqual(gk["full_model_status"], STATUS_COMPLETE)
        self.assertEqual(row["terminal_label"], FINAL_APPROVED)

    def test_v16_result_without_gatekeeper_pass_is_downgraded(self):
        """v16 result with final_label=FINAL_APPROVED but no gatekeeper pass → downgraded."""
        result = {
            "final_label":   FINAL_APPROVED,
            "skill_results": [{"skill": "some_skill", "label": FINAL_APPROVED}],
            "blockers":      [],
        }
        verify_v16_result(result)
        self.assertNotEqual(result["final_label"], FINAL_APPROVED)
        self.assertEqual(result["final_label"], MODEL_QUALIFIED_HOLD)
        self.assertTrue(any("FMCG" in b for b in result.get("blockers", [])))

    def test_missing_cal_prob_blocks_final_approved(self):
        """Missing calibrated_probability → INCOMPLETE → downgrade."""
        row = _base_row()
        row["calibrated_probability"] = None
        gk = _apply(row)
        self.assertNotEqual(row["terminal_label"], FINAL_APPROVED)
        self.assertEqual(gk["full_model_status"], STATUS_INCOMPLETE)

    def test_source_grade_unobtainable_blocks(self):
        """UNOBTAINABLE source grade → REJECT."""
        row = _base_row()
        row["source_grade"] = "UNOBTAINABLE"
        gk = _apply(row)
        self.assertEqual(row["terminal_label"], MODEL_QUALIFIED_HOLD)
        self.assertTrue(any("UNOBTAINABLE" in b for b in gk["blockers"]))

    def test_fmcg_no_pass_blocker_appended_on_downgrade(self):
        """FMCG:NO_GATEKEEPER_PASS blocker is always appended when downgrading."""
        row = _base_row()
        row["model_status"] = "NO_REGISTERED_MODEL"
        apply_gatekeeper(row, governance_hash="test-hash")
        self.assertTrue(any("FMCG:NO_GATEKEEPER_PASS" in b
                            for b in row.get("blockers", [])))

    def test_contradiction_in_market_gate_blocks(self):
        """MARKET_CONTRADICTION market_status → contradiction audit FAIL."""
        row = _base_row()
        row["gates"]["market_gate"]["market_status"] = "MARKET_CONTRADICTION"
        gk = _apply(row)
        self.assertEqual(row["terminal_label"], MODEL_QUALIFIED_HOLD)
        self.assertTrue(any("CONTRADICTION_AUDIT" in b for b in gk["blockers"]))


# ---------------------------------------------------------------------------
# Unit tests for individual gate checks
# ---------------------------------------------------------------------------

class TestUnitRoleStatus(unittest.TestCase):

    def test_dependency_conflict_is_fail(self):
        row = _base_row()
        row["gates"]["status_role"]["role_status"] = "DEPENDENCY_CONFLICT"
        gk = _apply(row)
        self.assertEqual(gk["gate_results"]["role_status"]["status"], GATE_FAIL)

    def test_stale_is_hold(self):
        row = _base_row()
        row["gates"]["status_role"]["role_status"] = "STALE"
        gk = _apply(row)
        self.assertEqual(gk["gate_results"]["role_status"]["status"], GATE_HOLD)

    def test_confirmed_is_pass(self):
        row = _base_row()
        row["gates"]["status_role"]["role_status"] = "CONFIRMED"
        gk = _apply(row)
        self.assertEqual(gk["gate_results"]["role_status"]["status"], GATE_PASS)


class TestUnitMarketIdentity(unittest.TestCase):

    def test_missing_player_fails(self):
        row = _base_row()
        row.pop("player", None)
        row.pop("player_name", None)
        row.pop("player_id", None)
        gk = _apply(row)
        self.assertEqual(gk["gate_results"]["market_identity"]["status"], GATE_FAIL)
        self.assertIn("player", str(gk["gate_results"]["market_identity"]["evidence"]["missing_fields"]))

    def test_missing_sport_fails(self):
        row = _base_row()
        row["sport"] = None
        gk = _apply(row)
        self.assertEqual(gk["gate_results"]["market_identity"]["status"], GATE_FAIL)


class TestUnitCalibrationStatus(unittest.TestCase):

    def test_out_of_range_cal_prob_fails(self):
        row = _base_row()
        row["calibrated_probability"] = 1.5
        gk = _apply(row)
        self.assertEqual(gk["gate_results"]["calibrated_probability"]["status"], GATE_FAIL)


    def test_cal_prob_zero_is_rejected(self):
        """p=0.0 is structurally degenerate — strict exclusive (0,1) rejects it (v1.1)."""
        row = _base_row()
        row["calibrated_probability"] = 0.0
        gk = _apply(row)
        self.assertEqual(gk["gate_results"]["calibrated_probability"]["status"], GATE_FAIL)
        self.assertTrue(any("OUT_OF_RANGE_EXCLUSIVE" in b for b in gk["blockers"]))

    def test_cal_prob_one_is_rejected(self):
        """p=1.0 is structurally degenerate — strict exclusive (0,1) rejects it (v1.1)."""
        row = _base_row()
        row["calibrated_probability"] = 1.0
        gk = _apply(row)
        self.assertEqual(gk["gate_results"]["calibrated_probability"]["status"], GATE_FAIL)
        self.assertTrue(any("OUT_OF_RANGE_EXCLUSIVE" in b for b in gk["blockers"]))

    def test_cal_prob_near_zero_is_valid(self):
        """p=0.001 is in the interior — must pass strict exclusive bounds."""
        row = _base_row()
        row["calibrated_probability"] = 0.001
        gk = _apply(row)
        self.assertEqual(gk["gate_results"]["calibrated_probability"]["status"], GATE_PASS)

    def test_cal_prob_near_one_is_valid(self):
        """p=0.999 is in the interior — must pass strict exclusive bounds."""
        row = _base_row()
        row["calibrated_probability"] = 0.999
        gk = _apply(row)
        self.assertEqual(gk["gate_results"]["calibrated_probability"]["status"], GATE_PASS)

    def test_cal_prob_out_of_range_above_one_fails(self):
        """p=1.5 is out of range — must fail."""
        row = _base_row()
        row["calibrated_probability"] = 1.5
        gk = _apply(row)
        self.assertEqual(gk["gate_results"]["calibrated_probability"]["status"], GATE_FAIL)

    def test_provisional_model_is_hold(self):
        row = _base_row()
        row["model_status"] = "PROVISIONAL"
        gk = _apply(row)
        self.assertEqual(gk["gate_results"]["calibrated_probability"]["status"], GATE_HOLD)


class TestUnitInvalidation(unittest.TestCase):

    def test_no_signals_gives_no_invalidation(self):
        row = _base_row()
        gk = _apply(row)
        self.assertFalse(gk["invalidation_state"]["is_invalidated"])

    def test_goalie_changed_invalidates(self):
        row = _base_row(goalie_changed=True)
        gk = _apply(row)
        self.assertEqual(gk["full_model_status"], STATUS_INVALIDATED)

    def test_settlement_status_changed_invalidates(self):
        row = _base_row(settlement_status_changed=True)
        gk = _apply(row)
        self.assertEqual(gk["full_model_status"], STATUS_INVALIDATED)


class TestUnitContractIdentifiers(unittest.TestCase):

    def test_gatekeeper_version_constant(self):
        self.assertEqual(fmcg.GATEKEEPER_VERSION, "WOW-FMCG-v1.1")

    def test_can_execute_always_false(self):
        self.assertIs(CAN_EXECUTE, False)
        row = _base_row()
        gk = _apply(row)
        self.assertIs(gk["can_execute"], False)

    def test_patch_precedence_105(self):
        self.assertEqual(fmcg.PATCH_PRECEDENCE, 105)

    def test_patch_id(self):
        self.assertEqual(fmcg.PATCH_ID, "WOW-PATCH-FMCG-v1.1")

    def test_nested_custom_gpt_not_required(self):
        """Host abstraction: nested Custom-GPT must not be required (v1.1)."""
        self.assertIs(fmcg.NESTED_CUSTOM_GPT_REQUIRED, False)
        row = _base_row()
        gk = _apply(row)
        self.assertIs(gk["nested_custom_gpt_required"], False)

    def test_cross_sport_selector_proposed_not_binding(self):
        """Cross-sport selector is PROPOSED_NOT_BINDING — not wired into scoring."""
        self.assertEqual(fmcg.CROSS_SPORT_HIGH_PROBABILITY_SELECTOR_STATUS, "PROPOSED_NOT_BINDING")
        row = _base_row()
        gk = _apply(row)
        self.assertEqual(gk["cross_sport_selector_status"], "PROPOSED_NOT_BINDING")

    def test_kalshi_recovery_mode_constant_present(self):
        self.assertEqual(fmcg.KALSHI_RECOVERY_MODE, "ACTIVE")


# ---------------------------------------------------------------------------
# v1.1 gate tests
# ---------------------------------------------------------------------------

class TestV11CalibrationHealthGate(unittest.TestCase):
    """Gate 15 — calibration health precheck (Layer 0.5 result)."""

    def test_suppress_grade_blocks(self):
        row = _base_row()
        row["gates"]["calibration_health"] = {"health_grade": "SUPPRESS", "detail": "too many failures"}
        gk = _apply(row)
        self.assertEqual(gk["gate_results"]["calibration_health"]["status"], GATE_FAIL)
        self.assertTrue(any("CALIBRATION_HEALTH:SUPPRESS" in b for b in gk["blockers"]))
        self.assertEqual(row["terminal_label"], MODEL_QUALIFIED_HOLD)

    def test_watch_grade_holds(self):
        row = _base_row()
        row["gates"]["calibration_health"] = {"health_grade": "WATCH", "detail": "warning"}
        gk = _apply(row)
        self.assertEqual(gk["gate_results"]["calibration_health"]["status"], GATE_HOLD)
        self.assertTrue(any("CALIBRATION_HEALTH:WATCH" in b for b in gk["blockers"]))

    def test_green_grade_passes(self):
        row = _base_row()
        row["gates"]["calibration_health"] = {"health_grade": "GREEN", "detail": "healthy"}
        gk = _apply(row)
        self.assertEqual(gk["gate_results"]["calibration_health"]["status"], GATE_PASS)
        self.assertFalse(any("CALIBRATION_HEALTH" in b for b in gk["blockers"]))

    def test_absent_calibration_health_gate_skips(self):
        """Absent calibration_health gate skips gracefully (backward compat)."""
        row = _base_row()
        # No calibration_health in gates
        gk = _apply(row)
        result = gk["gate_results"]["calibration_health"]
        self.assertEqual(result["status"], GATE_SKIP)
        # Skipped gate must not add a blocker
        self.assertFalse(any("CALIBRATION_HEALTH" in b for b in gk["blockers"]))

    def test_data_gap_grade_passes(self):
        row = _base_row()
        row["gates"]["calibration_health"] = {"health_grade": "DATA_GAP"}
        gk = _apply(row)
        self.assertEqual(gk["gate_results"]["calibration_health"]["status"], GATE_PASS)


class TestV11BidirectionalMoreLess(unittest.TestCase):
    """Gate 16 — bidirectional MORE/LESS enforcement."""

    def test_prop_with_both_sides_evaluated_passes(self):
        row = _base_row()
        row["prop_type"] = "hits"
        row["gates"]["bidirectional_analysis"] = {
            "both_sides_evaluated": True,
            "more_evaluated": True,
            "less_evaluated": True,
        }
        gk = _apply(row)
        self.assertEqual(gk["gate_results"]["bidirectional_sides"]["status"], GATE_PASS)

    def test_prop_missing_less_side_holds(self):
        row = _base_row()
        row["prop_type"] = "hits"
        row["gates"]["bidirectional_analysis"] = {
            "both_sides_evaluated": False,
            "more_evaluated": True,
            "less_evaluated": False,
        }
        gk = _apply(row)
        self.assertEqual(gk["gate_results"]["bidirectional_sides"]["status"], GATE_HOLD)
        self.assertTrue(any("BIDIRECTIONAL:MISSING_SIDES" in b for b in gk["blockers"]))

    def test_prop_missing_more_side_holds(self):
        row = _base_row()
        row["prop_type"] = "pts"
        row["gates"]["bidirectional_analysis"] = {
            "both_sides_evaluated": False,
            "more_evaluated": False,
            "less_evaluated": True,
        }
        gk = _apply(row)
        self.assertEqual(gk["gate_results"]["bidirectional_sides"]["status"], GATE_HOLD)

    def test_prop_row_field_false_holds(self):
        row = _base_row()
        row["prop_type"] = "strikeouts"
        row["bidirectional_evaluation_complete"] = False
        gk = _apply(row)
        self.assertEqual(gk["gate_results"]["bidirectional_sides"]["status"], GATE_HOLD)
        self.assertTrue(any("BIDIRECTIONAL" in b for b in gk["blockers"]))

    def test_prop_row_field_true_passes(self):
        row = _base_row()
        row["prop_type"] = "strikeouts"
        row["bidirectional_evaluation_complete"] = True
        gk = _apply(row)
        self.assertEqual(gk["gate_results"]["bidirectional_sides"]["status"], GATE_PASS)

    def test_absent_bidirectional_gate_skips(self):
        """No bidirectional gate data → SKIP (backward compat)."""
        row = _base_row()
        row["prop_type"] = "hits"
        # No bidirectional_analysis and no bidirectional_evaluation_complete
        gk = _apply(row)
        self.assertEqual(gk["gate_results"]["bidirectional_sides"]["status"], GATE_SKIP)
        self.assertFalse(any("BIDIRECTIONAL" in b for b in gk["blockers"]))


class TestV11ProbabilityLedgerGate(unittest.TestCase):
    """Gate 18 — probability component ledger + shrinkage verdict."""

    def test_calibrated_status_passes(self):
        row = _base_row()
        row["gates"]["prob_ledger"] = {
            "calibration_status": "CALIBRATED",
            "shrinkage_applied": True,
            "shrinkage_required": False,
            "missing_required_components": [],
        }
        gk = _apply(row)
        self.assertEqual(gk["gate_results"]["prob_ledger"]["status"], GATE_PASS)

    def test_uncalibrated_status_holds(self):
        row = _base_row()
        row["gates"]["prob_ledger"] = {
            "calibration_status": "UNCALIBRATED",
            "shrinkage_applied": False,
            "shrinkage_required": False,
        }
        gk = _apply(row)
        self.assertEqual(gk["gate_results"]["prob_ledger"]["status"], GATE_HOLD)
        self.assertTrue(any("PROB_LEDGER:NOT_CALIBRATED" in b for b in gk["blockers"]))

    def test_proxy_only_holds(self):
        row = _base_row()
        row["gates"]["prob_ledger"] = {"calibration_status": "PROXY_ONLY"}
        gk = _apply(row)
        self.assertEqual(gk["gate_results"]["prob_ledger"]["status"], GATE_HOLD)

    def test_shrinkage_required_but_not_applied_holds(self):
        row = _base_row()
        row["gates"]["prob_ledger"] = {
            "calibration_status": "CALIBRATED",
            "shrinkage_applied": False,
            "shrinkage_required": True,
        }
        gk = _apply(row)
        self.assertEqual(gk["gate_results"]["prob_ledger"]["status"], GATE_HOLD)
        self.assertTrue(any("SHRINKAGE_REQUIRED_NOT_APPLIED" in b for b in gk["blockers"]))

    def test_absent_prob_ledger_gate_skips(self):
        """Absent prob_ledger gate → SKIP (backward compat)."""
        row = _base_row()
        gk = _apply(row)
        result = gk["gate_results"]["prob_ledger"]
        self.assertEqual(result["status"], GATE_SKIP)
        self.assertFalse(any("PROB_LEDGER" in b for b in gk["blockers"]))


class TestV11SessionDirectionalExposure(unittest.TestCase):
    """Gate 19 — session directional exposure (separate from structural correlation)."""

    def test_session_block_fails(self):
        row = _base_row()
        row["gates"]["directional_exposure"] = {
            "session_verdict": "SESSION_BLOCK",
            "dominant_count": 7,
            "dominant_script_type": "OVER",
        }
        gk = _apply(row)
        self.assertEqual(gk["gate_results"]["session_directional_exposure"]["status"], GATE_FAIL)
        self.assertTrue(any("SESSION_DIRECTIONAL:BLOCK" in b for b in gk["blockers"]))
        self.assertEqual(row["terminal_label"], MODEL_QUALIFIED_HOLD)

    def test_session_warning_holds(self):
        row = _base_row()
        row["gates"]["directional_exposure"] = {
            "session_verdict": "SESSION_WARNING",
            "dominant_count": 5,
            "dominant_script_type": "OVER",
        }
        gk = _apply(row)
        self.assertEqual(gk["gate_results"]["session_directional_exposure"]["status"], GATE_HOLD)
        self.assertTrue(any("SESSION_DIRECTIONAL:WARNING" in b for b in gk["blockers"]))

    def test_no_session_exposure_passes(self):
        row = _base_row()
        row["gates"]["directional_exposure"] = {
            "session_verdict": "CLEAR",
            "dominant_count": 2,
        }
        gk = _apply(row)
        self.assertEqual(gk["gate_results"]["session_directional_exposure"]["status"], GATE_PASS)

    def test_absent_directional_exposure_skips(self):
        """No directional_exposure gate → SKIP (backward compat)."""
        row = _base_row()
        gk = _apply(row)
        result = gk["gate_results"]["session_directional_exposure"]
        self.assertEqual(result["status"], GATE_SKIP)
        self.assertFalse(any("SESSION_DIRECTIONAL" in b for b in gk["blockers"]))

    def test_structural_slip_correlation_is_separate(self):
        """
        Slip correlation (downstream governor) must not be conflated with
        session/directional exposure gate.  Both can be present independently.
        """
        row = _base_row()
        # Session gate: clear
        row["gates"]["directional_exposure"] = {"session_verdict": "CLEAR"}
        # Slip structure gate present: independent
        row["gates"]["slip_structure"] = {"passed": True, "correlation_score": 0.1}
        gk = _apply(row)
        self.assertEqual(gk["gate_results"]["session_directional_exposure"]["status"], GATE_PASS)


class TestV11PregameSnapshotGate(unittest.TestCase):
    """Gate 20 — pregame snapshot / final refresh enforcement."""

    def test_final_refresh_passed_true_passes(self):
        row = _base_row()
        row["final_refresh_passed"] = True
        gk = _apply(row)
        self.assertEqual(gk["gate_results"]["pregame_snapshot"]["status"], GATE_PASS)

    def test_final_refresh_passed_false_holds_for_money_row(self):
        """FINAL_APPROVED row with refresh=False must be held."""
        row = _base_row()
        row["final_refresh_passed"] = False
        row["terminal_label"] = FINAL_APPROVED
        gk = _apply(row)
        self.assertEqual(gk["gate_results"]["pregame_snapshot"]["status"], GATE_HOLD)
        self.assertTrue(any("PREGAME_SNAPSHOT:FINAL_REFRESH_NOT_PASSED" in b
                            for b in gk["blockers"]))

    def test_final_refresh_required_without_passed_flag_holds(self):
        row = _base_row()
        row["final_refresh_required"] = True
        gk = _apply(row)
        self.assertEqual(gk["gate_results"]["pregame_snapshot"]["status"], GATE_HOLD)
        self.assertTrue(any("FINAL_REFRESH_REQUIRED_NOT_RESOLVED" in b
                            for b in gk["blockers"]))

    def test_vacuous_refresh_on_money_row_holds(self):
        """A vacuous-pass final_refresh on a FINAL_APPROVED row must be held."""
        row = _base_row()
        row["gates"]["pp_final_refresh"] = {"code": "FINAL_REFRESH_VACUOUS"}
        row["terminal_label"] = FINAL_APPROVED
        gk = _apply(row)
        self.assertEqual(gk["gate_results"]["pregame_snapshot"]["status"], GATE_HOLD)
        self.assertTrue(any("VACUOUS_REFRESH_ON_MONEY_ROW" in b for b in gk["blockers"]))

    def test_refresh_clear_code_passes(self):
        row = _base_row()
        row["gates"]["pp_final_refresh"] = {"code": "FINAL_REFRESH_CLEAR"}
        gk = _apply(row)
        self.assertEqual(gk["gate_results"]["pregame_snapshot"]["status"], GATE_PASS)

    def test_absent_refresh_data_skips(self):
        """No refresh data → SKIP (backward compat)."""
        row = _base_row()
        gk = _apply(row)
        result = gk["gate_results"]["pregame_snapshot"]
        self.assertEqual(result["status"], GATE_SKIP)
        self.assertFalse(any("PREGAME_SNAPSHOT" in b for b in gk["blockers"]))


class TestV11CanonicalCeilingResolver(unittest.TestCase):
    """canonical_ceiling_resolve() used by all three final-row paths."""

    def test_canonical_function_exists(self):
        self.assertTrue(callable(fmcg.canonical_ceiling_resolve))

    def test_none_inputs_handled(self):
        self.assertIsNone(fmcg.canonical_ceiling_resolve(None, None))
        self.assertEqual(fmcg.canonical_ceiling_resolve(None, FINAL_APPROVED), FINAL_APPROVED)
        self.assertEqual(fmcg.canonical_ceiling_resolve(FINAL_APPROVED, None), FINAL_APPROVED)

    def test_more_restrictive_wins(self):
        """MODEL_QUALIFIED_HOLD is more restrictive than FINAL_APPROVED."""
        result = fmcg.canonical_ceiling_resolve(FINAL_APPROVED, MODEL_QUALIFIED_HOLD)
        self.assertEqual(result, MODEL_QUALIFIED_HOLD)

    def test_equal_inputs_stable(self):
        result = fmcg.canonical_ceiling_resolve(MODEL_QUALIFIED_HOLD, MODEL_QUALIFIED_HOLD)
        self.assertEqual(result, MODEL_QUALIFIED_HOLD)

    def test_apply_gatekeeper_uses_canonical_resolver(self):
        """apply_gatekeeper downgrade uses canonical_ceiling_resolve."""
        row = _base_row(model_status="NO_REGISTERED_MODEL")
        apply_gatekeeper(row, governance_hash="test-hash")
        # Downgrade must not upgrade past entry ceiling
        self.assertEqual(row["terminal_label"], MODEL_QUALIFIED_HOLD)

    def test_verify_cc_envelope_uses_canonical_resolver(self):
        """verify_cc_envelope downgrade uses canonical_ceiling_resolve."""
        envelope = {
            "engine_label": FINAL_APPROVED,
            "engine_result": {},  # No gatekeeper result
        }
        result = verify_cc_envelope(envelope)
        self.assertFalse(result)
        self.assertEqual(envelope["engine_label"], MODEL_QUALIFIED_HOLD)

    def test_verify_v16_result_uses_canonical_resolver(self):
        """verify_v16_result downgrade uses canonical_ceiling_resolve."""
        result = {"final_label": FINAL_APPROVED, "skill_results": []}
        verify_v16_result(result)
        self.assertEqual(result["final_label"], MODEL_QUALIFIED_HOLD)
        self.assertTrue(any("FMCG:V16" in b for b in result.get("blockers", [])))

    def test_more_restrictive_alias_delegates_to_canonical(self):
        """_more_restrictive is an alias for canonical_ceiling_resolve."""
        a = fmcg.canonical_ceiling_resolve(FINAL_APPROVED, MODEL_QUALIFIED_HOLD)
        b = fmcg._more_restrictive(FINAL_APPROVED, MODEL_QUALIFIED_HOLD)
        self.assertEqual(a, b)


class TestV11HostAbstraction(unittest.TestCase):
    """Req 1 — host abstraction; engine must not depend on legacy_platform app lookup."""

    def test_nested_custom_gpt_required_is_false(self):
        self.assertIs(fmcg.NESTED_CUSTOM_GPT_REQUIRED, False)

    def test_evaluate_output_carries_nested_gpt_false(self):
        row = _base_row()
        gk = _apply(row)
        self.assertIn("nested_custom_gpt_required", gk)
        self.assertIs(gk["nested_custom_gpt_required"], False)

    def test_cross_sport_selector_not_binding(self):
        """Cross-sport selector must be PROPOSED_NOT_BINDING — not enforced."""
        self.assertEqual(
            fmcg.CROSS_SPORT_HIGH_PROBABILITY_SELECTOR_STATUS,
            "PROPOSED_NOT_BINDING",
        )
        # Confirm it is absent from gate logic (no gate in results named cross_sport)
        row = _base_row()
        gk = _apply(row)
        gate_names = list(gk["gate_results"].keys())
        self.assertFalse(any("cross_sport" in g for g in gate_names))


class TestV11SourceTimestampGate(unittest.TestCase):
    """Gate 17 — source timestamp/freshness grading via worst_critical field."""

    def _make_row_with_source_grade(self, worst_critical, critical_has_ts=True):
        """Build a row with row['gates']['source_grade'] populated correctly."""
        row = _base_row()
        row["gates"]["source_grade"] = {
            "passed":          critical_has_ts and worst_critical not in {"N/T", "NT"},
            "worst_critical":  worst_critical,
            "code":            "SOURCE_GRADE_OK" if worst_critical == "A" else f"SOURCE_GRADE_DEGRADED:{worst_critical}",
            "source_grades": [
                {
                    "name":        "test-source",
                    "source_type": "stat_feed",
                    "role":        "l5_l10",
                    "grade":       worst_critical,
                    "effective_grade": worst_critical,
                    "has_timestamp": critical_has_ts,
                    "corroborated": False,
                    "is_critical":  True,
                    "reconciliation_flags": [],
                }
            ],
            "critical_grades": [worst_critical],
            "source_conflict": False,
            "reconciliation_blockers": [],
        }
        return row

    def test_nt_worst_critical_holds(self):
        """worst_critical == 'N/T' → HOLD (no timestamp on critical source)."""
        row = self._make_row_with_source_grade("N/T")
        gk = _apply(row)
        self.assertEqual(gk["gate_results"]["source_timestamp_grading"]["status"], GATE_HOLD)
        self.assertTrue(any("SOURCE_TIMESTAMP" in b for b in gk["blockers"]))

    def test_grade_a_passes(self):
        """worst_critical == 'A' and all timestamped → PASS."""
        row = self._make_row_with_source_grade("A")
        gk = _apply(row)
        self.assertEqual(gk["gate_results"]["source_timestamp_grading"]["status"], GATE_PASS)
        self.assertFalse(any("SOURCE_TIMESTAMP" in b for b in gk["blockers"]))

    def test_grade_b_passes_on_timestamp(self):
        """worst_critical == 'B' with timestamp → PASS (B is not N/T)."""
        row = self._make_row_with_source_grade("B", critical_has_ts=True)
        gk = _apply(row)
        self.assertEqual(gk["gate_results"]["source_timestamp_grading"]["status"], GATE_PASS)

    def test_critical_no_timestamp_holds(self):
        """Critical source with has_timestamp=False → HOLD even if worst_critical is not N/T."""
        row = _base_row()
        row["gates"]["source_grade"] = {
            "worst_critical": "B",
            "code": "SOURCE_GRADE_B_UNCORROBORATED",
            "source_grades": [
                {
                    "name":        "timestampless-source",
                    "source_type": "stat_feed",
                    "role":        "l5_l10",
                    "grade":       "B",
                    "effective_grade": "B",
                    "has_timestamp": False,
                    "corroborated":  False,
                    "is_critical":   True,
                    "reconciliation_flags": [],
                }
            ],
            "critical_grades": ["B"],
            "source_conflict": False,
        }
        gk = _apply(row)
        self.assertEqual(gk["gate_results"]["source_timestamp_grading"]["status"], GATE_HOLD)
        self.assertTrue(any("SOURCE_TIMESTAMP" in b for b in gk["blockers"]))

    def test_non_critical_no_timestamp_passes(self):
        """Non-critical source with no timestamp does NOT trigger HOLD."""
        row = _base_row()
        row["gates"]["source_grade"] = {
            "worst_critical": "A",
            "code": "SOURCE_GRADE_OK",
            "source_grades": [
                {
                    "name":        "non-critical-no-ts",
                    "source_type": "news",
                    "role":        "context",
                    "grade":       "C",
                    "effective_grade": "C",
                    "has_timestamp": False,
                    "corroborated":  False,
                    "is_critical":   False,
                    "reconciliation_flags": [],
                }
            ],
            "critical_grades": ["A"],
            "source_conflict": False,
        }
        gk = _apply(row)
        self.assertEqual(gk["gate_results"]["source_timestamp_grading"]["status"], GATE_PASS)

    def test_absent_source_grade_gate_skips(self):
        """Absent source_grade gate → SKIP (backward compat)."""
        row = _base_row()
        # No source_grade in gates
        gk = _apply(row)
        self.assertEqual(gk["gate_results"]["source_timestamp_grading"]["status"], GATE_SKIP)
        self.assertFalse(any("SOURCE_TIMESTAMP" in b for b in gk["blockers"]))

    def test_row_level_nt_flag_holds_when_gate_absent(self):
        """row['source_timestamp_grade'] == 'N/T' is honored when gate dict absent."""
        row = _base_row()
        row["source_timestamp_grade"] = "N/T"
        gk = _apply(row)
        self.assertEqual(gk["gate_results"]["source_timestamp_grading"]["status"], GATE_HOLD)

    def test_legacy_grade_type_field_not_used(self):
        """Confirm gate does NOT read the nonexistent 'grade_type' field — N/T must come from worst_critical."""
        row = _base_row()
        # Populate a source_grade dict with grade_type but NOT worst_critical
        row["gates"]["source_grade"] = {
            "grade_type":    "N/T",   # legacy field — should NOT be read
            "worst_critical": "A",    # actual field — says grade is fine
            "code":          "SOURCE_GRADE_OK",
            "source_grades": [],
            "critical_grades": ["A"],
            "source_conflict": False,
        }
        gk = _apply(row)
        # If gate correctly reads worst_critical="A" → PASS; not "grade_type"="N/T" → HOLD
        self.assertEqual(gk["gate_results"]["source_timestamp_grading"]["status"], GATE_PASS)


class TestV11StrictProbBoundsEndToEnd(unittest.TestCase):
    """Req 6 — strict exclusive (0,1) bounds through full evaluate() path."""

    def test_p_zero_downgrade_to_hold(self):
        row = _base_row()
        row["calibrated_probability"] = 0.0
        apply_gatekeeper(row)
        self.assertEqual(row["terminal_label"], MODEL_QUALIFIED_HOLD)

    def test_p_one_downgrade_to_hold(self):
        row = _base_row()
        row["calibrated_probability"] = 1.0
        apply_gatekeeper(row)
        self.assertEqual(row["terminal_label"], MODEL_QUALIFIED_HOLD)

    def test_p_epsilon_above_zero_qualifies(self):
        row = _base_row()
        row["calibrated_probability"] = 0.001
        gk = _apply(row)
        self.assertEqual(gk["qualification_result"], QUAL_PASS)

    def test_p_epsilon_below_one_qualifies(self):
        row = _base_row()
        row["calibrated_probability"] = 0.999
        gk = _apply(row)
        self.assertEqual(gk["qualification_result"], QUAL_PASS)

    def test_bounds_rule_in_output(self):
        """probability_summary must carry the strict bounds rule label."""
        row = _base_row()
        gk = _apply(row)
        rule = gk["probability_summary"].get("bounds_rule", "")
        self.assertIn("exclusive", rule)


if __name__ == "__main__":
    unittest.main()
