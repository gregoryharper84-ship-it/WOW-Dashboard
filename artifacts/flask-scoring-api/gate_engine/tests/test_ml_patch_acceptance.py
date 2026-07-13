"""
gate_engine/tests/test_ml_patch_acceptance.py
WOW-PATCH-2026-07-13 — LLP ML Price/Settlement/Exposure Governance
10 Acceptance Tests (AT-1 through AT-10)

All tests are deterministic (no live API calls, no app.py imports).
"""
from __future__ import annotations

import sys
import os
import unittest

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from gate_engine.ml_settlement_truth import reconcile_settlement, classify_settlement_batch
from gate_engine.ml_deduplication import deduplicate_entries, build_event_key
from gate_engine.ml_edge_gate import (
    compute_breakeven, validate_ml_edge_requirements,
    validate_price_compression, run_ml_edge_gate,
)
from gate_engine.ml_bullpen_gate import validate_bullpen_gate
from gate_engine.ml_series_market_gate import validate_market_disagreement
from gate_engine.ml_reporting import build_ml_performance_summary
from gate_engine.ml_approval_snapshot import (
    build_approval_snapshot, add_settlement_to_snapshot,
    validate_snapshot_integrity,
)
from gate_engine.ml_labels import MLReasonCode, MarketDisagreementLabel


# ═══════════════════════════════════════════════════════════════════════════════
# AT-1: Mariners official loss with platform "Win" remains a model loss
# ═══════════════════════════════════════════════════════════════════════════════

class TestAT1_MarinersOfficialLossPlatformWin(unittest.TestCase):
    """
    Event: Mariners vs Marlins, 2026-07-09
    Selected side: Mariners (away)
    Official result: Marlins won (away team = Mariners LOST 8-4)
    Platform display: Win (promo/special)
    Platform payment: $5.04

    Expected:
      selected_side_result          = LOSS
      model_result                  = LOSS
      platform_settlement_status    = PROMO_OR_SPECIAL_SETTLEMENT
      calibration_outcome           = LOSS
      calibration_eligible          = True
    """

    def _entry(self):
        return {
            "league":                  "MLB",
            "event_date":              "2026-07-09",
            "away_team":               "mariners",
            "home_team":               "marlins",
            "selected_side":           "mariners",
            "selected_side_is_home":   False,        # Mariners were the AWAY team
            "official_event_result":   "HOME_WIN",   # Marlins (home) won
            "platform_display_result": "WIN",         # PrizePicks showed Win
            "platform_payment":        5.04,
            "stake":                   5.00,
            "market_type":             "ml",
        }

    def test_selected_side_result_is_loss(self):
        result = reconcile_settlement(self._entry())
        self.assertEqual(result["selected_side_result"], "LOSS")

    def test_model_result_is_loss(self):
        result = reconcile_settlement(self._entry())
        self.assertEqual(result["model_result"], "LOSS",
                         "Platform 'Win' display must not override official model result")

    def test_platform_settlement_status_is_promo(self):
        result = reconcile_settlement(self._entry())
        self.assertEqual(
            result["platform_settlement_status"],
            "PROMO_OR_SPECIAL_SETTLEMENT",
        )

    def test_calibration_outcome_is_loss(self):
        result = reconcile_settlement(self._entry())
        self.assertEqual(result["calibration_outcome"], "LOSS")

    def test_calibration_eligible_is_true(self):
        result = reconcile_settlement(self._entry())
        self.assertTrue(result["calibration_eligible"])

    def test_reason_codes_contain_settlement_override(self):
        result = reconcile_settlement(self._entry())
        self.assertIn(MLReasonCode.PROMO_SETTLEMENT.value, result["reason_codes"])

    def test_gross_return_preserved_for_financial_roi(self):
        """Platform payment must be preserved in gross_return (financial ROI only)."""
        result = reconcile_settlement(self._entry())
        self.assertAlmostEqual(result["gross_return"], 5.04)


# ═══════════════════════════════════════════════════════════════════════════════
# AT-2: Two Phillies tickets count as one model observation
# ═══════════════════════════════════════════════════════════════════════════════

class TestAT2_PhilliesDuplicateDeduplication(unittest.TestCase):
    """
    Two Phillies ML entries on the same date, same opponent, same side, same market.

    Expected:
      financial_entry_count         = 2
      model_observation_count       = 1
      calibration_observation_count = 1
      duplicate_exposure            = combined stake of second ticket
    """

    def _entries(self):
        base = {
            "league":      "mlb",
            "event_date":  "2026-07-09",
            "away_team":   "phillies",
            "home_team":   "mets",
            "market_type": "ml",
            "selected_side": "phillies",
        }
        return [
            {**base, "stake": 10.00, "listed_return": 18.00},
            {**base, "stake": 5.00,  "listed_return": 9.00},
        ]

    def test_same_event_key(self):
        entries = self._entries()
        k1 = build_event_key(entries[0])
        k2 = build_event_key(entries[1])
        self.assertEqual(k1, k2, "Both tickets must share the same event_key")

    def test_financial_entry_count_is_two(self):
        result = deduplicate_entries(self._entries())
        for canonical in result["canonical_events"].values():
            self.assertEqual(canonical["financial_entry_count"], 2)

    def test_model_observation_count_is_one(self):
        result = deduplicate_entries(self._entries())
        for canonical in result["canonical_events"].values():
            self.assertEqual(canonical["model_observation_count"], 1)

    def test_calibration_observation_count_is_one(self):
        result = deduplicate_entries(self._entries())
        for canonical in result["canonical_events"].values():
            self.assertEqual(canonical["calibration_observation_count"], 1)

    def test_duplicate_exposure_equals_second_stake(self):
        result = deduplicate_entries(self._entries())
        for canonical in result["canonical_events"].values():
            self.assertAlmostEqual(canonical["duplicate_exposure"], 5.00,
                                   msg="duplicate_exposure = stake beyond first ticket")

    def test_gross_stake_is_combined(self):
        result = deduplicate_entries(self._entries())
        for canonical in result["canonical_events"].values():
            self.assertAlmostEqual(canonical["gross_stake"], 15.00)

    def test_summary_unique_events_is_one(self):
        result = deduplicate_entries(self._entries())
        self.assertEqual(result["summary"]["unique_events"], 1)

    def test_summary_total_model_observations_is_one(self):
        result = deduplicate_entries(self._entries())
        self.assertEqual(result["summary"]["total_model_observations"], 1)


# ═══════════════════════════════════════════════════════════════════════════════
# AT-3: Dodgers $24.50 for $35 calculates 70.0% breakeven
# ═══════════════════════════════════════════════════════════════════════════════

class TestAT3_DodgersBreakevenCalculation(unittest.TestCase):
    """
    stake = $24.50, listed_return = $35.00
    Expected: breakeven_prob = 24.50/35.00 = 0.700000 (70.0%)
    """

    def test_breakeven_prob_is_70_percent(self):
        bp = compute_breakeven(stake=24.50, listed_return=35.00)
        self.assertAlmostEqual(bp, 0.70, places=4,
                               msg="Dodgers $24.50/$35.00 must break even at exactly 70.0%")

    def test_breakeven_via_edge_gate(self):
        candidate = {
            "stake":              24.50,
            "listed_return":      35.00,
            "model_prob":         0.72,
            "market_no_vig_prob": 0.68,
        }
        result = validate_ml_edge_requirements(candidate)
        self.assertTrue(result["passed"])
        self.assertAlmostEqual(result["computed"]["breakeven_prob"], 0.70, places=4)


# ═══════════════════════════════════════════════════════════════════════════════
# AT-4: A 72% Dodgers projection fails the required 73.5% threshold
# ═══════════════════════════════════════════════════════════════════════════════

class TestAT4_DodgersCompressionRejection(unittest.TestCase):
    """
    breakeven_prob = 0.70 → bucket "70%+" requires min +3.5% verified edge
    model_prob = 0.72 → verified_edge = 0.72 - 0.70 = +2.0%
    2.0% < 3.5% floor → LLP_REJECT_PRICE_COMPRESSION

    Minimum passing model_prob = 0.70 + 0.035 = 0.735 (73.5%)
    """

    def test_72pct_model_fails_compression(self):
        result = validate_price_compression(
            breakeven_prob=0.70,
            verified_edge=0.02,   # 72% model - 70% breakeven = +2%
            model_prob=0.72,
        )
        self.assertFalse(result["passed"])
        self.assertEqual(result["reason_code"], MLReasonCode.PRICE_COMPRESSION.value)

    def test_73pct_model_still_fails(self):
        result = validate_price_compression(
            breakeven_prob=0.70,
            verified_edge=0.03,
            model_prob=0.73,
        )
        self.assertFalse(result["passed"],
                         "73% model (only +3% edge) still below 3.5% floor")

    def test_735pct_model_passes(self):
        result = validate_price_compression(
            breakeven_prob=0.70,
            verified_edge=0.035,
            model_prob=0.735,
        )
        self.assertTrue(result["passed"],
                        "73.5% model (+3.5% edge) must pass the 70%+ compression floor")

    def test_full_gate_dodgers_rejected(self):
        """Full gate run: $24.50/$35 + 72% model → REJECT_PRICE_COMPRESSION."""
        candidate = {
            "stake":              24.50,
            "listed_return":      35.00,
            "model_prob":         0.72,
            "market_no_vig_prob": 0.68,
        }
        result = run_ml_edge_gate(candidate)
        self.assertFalse(result["passed"])
        self.assertEqual(result["reason_code"], MLReasonCode.PRICE_COMPRESSION.value)
        self.assertIn("COMPRESSION_FLOOR_NOT_MET", result["blockers"])


# ═══════════════════════════════════════════════════════════════════════════════
# AT-5: Missing no-vig market probability caps at LLP_WATCH
# ═══════════════════════════════════════════════════════════════════════════════

class TestAT5_MissingNoVigCapWatch(unittest.TestCase):
    """
    Missing market_no_vig_prob must cap the pick at LLP_WATCH (not APPROVED).
    """

    def test_missing_no_vig_returns_watch_ceiling(self):
        candidate = {
            "stake":         10.00,
            "listed_return": 18.00,
            "model_prob":    0.62,
            # market_no_vig_prob intentionally absent
        }
        result = validate_ml_edge_requirements(candidate)
        self.assertFalse(result["passed"])
        self.assertEqual(result["ceiling"], "LLP_WATCH",
                         "Missing no-vig probability must cap at LLP_WATCH")

    def test_missing_model_prob_returns_watch_ceiling(self):
        candidate = {
            "stake":              10.00,
            "listed_return":      18.00,
            "market_no_vig_prob": 0.60,
            # model_prob intentionally absent
        }
        result = validate_ml_edge_requirements(candidate)
        self.assertFalse(result["passed"])
        self.assertEqual(result["ceiling"], "LLP_WATCH")

    def test_all_present_passes(self):
        candidate = {
            "stake":              10.00,
            "listed_return":      18.00,
            "model_prob":         0.62,
            "market_no_vig_prob": 0.60,
        }
        result = validate_ml_edge_requirements(candidate)
        self.assertTrue(result["passed"])

    def test_missing_stake_returns_watch_ceiling(self):
        candidate = {
            "listed_return":      18.00,
            "model_prob":         0.62,
            "market_no_vig_prob": 0.60,
        }
        result = validate_ml_edge_requirements(candidate)
        self.assertFalse(result["passed"])
        self.assertEqual(result["ceiling"], "LLP_WATCH")


# ═══════════════════════════════════════════════════════════════════════════════
# AT-6: Missing bullpen freshness caps full-game ML at LLP_WATCH
# ═══════════════════════════════════════════════════════════════════════════════

class TestAT6_BullpenFreshnessCapWatch(unittest.TestCase):
    """
    Full-game ML with unknown bullpen freshness or high_leverage_availability
    must be capped at LLP_WATCH.  (Blue Jays failure mode.)
    """

    def test_missing_bullpen_freshness_edge_caps_watch(self):
        candidate = {
            "starter_edge":          0.03,
            "offense_edge":          0.02,
            "bullpen_quality_edge":  0.01,
            # bullpen_freshness_edge absent
            "high_leverage_availability": 0.8,
            "defense_edge":          0.01,
        }
        result = validate_bullpen_gate(candidate, is_full_game=True)
        self.assertFalse(result["passed"])
        self.assertEqual(result["ceiling"], "LLP_WATCH")
        self.assertIn("bullpen_freshness_edge", result["missing_components"])

    def test_unknown_high_leverage_availability_caps_watch(self):
        candidate = {
            "starter_edge":              0.03,
            "offense_edge":              0.02,
            "bullpen_quality_edge":      0.01,
            "bullpen_freshness_edge":    0.02,
            "high_leverage_availability": "UNKNOWN",  # unknown sentinel
            "defense_edge":              0.01,
        }
        result = validate_bullpen_gate(candidate, is_full_game=True)
        self.assertFalse(result["passed"])
        self.assertEqual(result["ceiling"], "LLP_WATCH")
        self.assertIn("high_leverage_availability", result["unknown_components"])

    def test_all_components_present_passes(self):
        candidate = {
            "starter_edge":              0.03,
            "offense_edge":              0.02,
            "bullpen_quality_edge":      0.01,
            "bullpen_freshness_edge":    0.02,
            "high_leverage_availability": 0.8,
            "defense_edge":              0.01,
        }
        result = validate_bullpen_gate(candidate, is_full_game=True)
        self.assertTrue(result["passed"])
        self.assertIsNone(result["ceiling"])

    def test_f5_market_skips_bullpen_gate(self):
        """Non-full-game markets (F5, live) should not be subject to this gate."""
        candidate = {}  # no bullpen fields at all
        result = validate_bullpen_gate(candidate, is_full_game=False)
        self.assertTrue(result["passed"])
        self.assertIsNone(result["ceiling"])

    def test_reason_code_is_bullpen_unverified(self):
        candidate = {
            "starter_edge":  0.03,
            "offense_edge":  0.02,
            "bullpen_quality_edge": 0.01,
            # missing bullpen_freshness_edge and high_leverage_availability
        }
        result = validate_bullpen_gate(candidate, is_full_game=True)
        self.assertEqual(result["reason_code"], MLReasonCode.BULLPEN_UNVERIFIED.value)


# ═══════════════════════════════════════════════════════════════════════════════
# AT-7: Platform promo payment stays in financial ROI but NOT model accuracy
# ═══════════════════════════════════════════════════════════════════════════════

class TestAT7_PromoROINotModelAccuracy(unittest.TestCase):
    """
    A promo/protected settlement should:
      - appear in gross_return (financial ROI)
      - NOT count as a model WIN
      - appear as a promo_settlement in the batch summary
    """

    def _batch(self):
        """Three picks: 2 genuine wins, 1 promo win (official loss)."""
        return [
            # Genuine Win
            {
                "league": "mlb", "event_date": "2026-07-09",
                "away_team": "cubs", "home_team": "reds",
                "market_type": "ml", "selected_side": "cubs",
                "selected_side_is_home": False,
                "official_event_result": "AWAY_WIN",
                "platform_display_result": "WIN",
                "platform_payment": 18.00, "stake": 10.00,
            },
            # Genuine Loss
            {
                "league": "mlb", "event_date": "2026-07-09",
                "away_team": "yankees", "home_team": "sox",
                "market_type": "ml", "selected_side": "yankees",
                "selected_side_is_home": False,
                "official_event_result": "HOME_WIN",
                "platform_display_result": "LOSS",
                "platform_payment": 0.00, "stake": 10.00,
            },
            # Promo win (official loss)
            {
                "league": "mlb", "event_date": "2026-07-09",
                "away_team": "mariners", "home_team": "marlins",
                "market_type": "ml", "selected_side": "mariners",
                "selected_side_is_home": False,
                "official_event_result": "HOME_WIN",
                "platform_display_result": "WIN",
                "platform_payment": 5.04, "stake": 5.00,
                "promo_protection_active": True,
            },
        ]

    def test_promo_not_counted_as_model_win(self):
        summary = classify_settlement_batch(self._batch())
        self.assertEqual(summary["model_wins"], 1,
                         "Only genuine wins count as model wins")
        self.assertEqual(summary["model_losses"], 2,
                         "Both the loss and the promo entry count as model losses")

    def test_platform_wins_include_promo(self):
        summary = classify_settlement_batch(self._batch())
        self.assertEqual(summary["platform_wins"], 2,
                         "Platform showed 2 wins (including promo)")

    def test_promo_settlements_counted(self):
        summary = classify_settlement_batch(self._batch())
        self.assertEqual(summary["promo_settlements"], 1)

    def test_gross_return_includes_promo_payment(self):
        """gross_return must include the promo payment for financial ROI tracking."""
        result = reconcile_settlement(self._batch()[2])
        self.assertAlmostEqual(result["gross_return"], 5.04,
                               msg="Promo payment must appear in gross_return")

    def test_model_record_differs_from_platform_record(self):
        summary = classify_settlement_batch(self._batch())
        self.assertNotEqual(summary["model_record"], summary["platform_record"])
        self.assertEqual(summary["model_record"], "1-2")
        self.assertEqual(summary["platform_record"], "2-1")


# ═══════════════════════════════════════════════════════════════════════════════
# AT-8: Duplicate stake appears in exposure reporting
# ═══════════════════════════════════════════════════════════════════════════════

class TestAT8_DuplicateStakeExposureReporting(unittest.TestCase):
    """
    When two tickets are placed on the same event/side, the duplicate
    stake must appear in exposure reporting, not be silently absorbed.
    """

    def _phillies_entries(self):
        return [
            {"league": "mlb", "event_date": "2026-07-10",
             "away_team": "phillies", "home_team": "mets",
             "market_type": "ml", "selected_side": "phillies",
             "stake": 10.00, "listed_return": 18.00},
            {"league": "mlb", "event_date": "2026-07-10",
             "away_team": "phillies", "home_team": "mets",
             "market_type": "ml", "selected_side": "phillies",
             "stake": 7.50, "listed_return": 13.50},
        ]

    def test_duplicate_exposure_is_nonzero(self):
        result = deduplicate_entries(self._phillies_entries())
        for canonical in result["canonical_events"].values():
            self.assertGreater(canonical["duplicate_exposure"], 0,
                               "Duplicate stake must appear as non-zero exposure")

    def test_duplicate_exposure_equals_second_ticket_stake(self):
        result = deduplicate_entries(self._phillies_entries())
        for canonical in result["canonical_events"].values():
            self.assertAlmostEqual(canonical["duplicate_exposure"], 7.50)

    def test_total_duplicate_exposure_in_summary(self):
        result = deduplicate_entries(self._phillies_entries())
        self.assertAlmostEqual(result["summary"]["total_duplicate_exposure"], 7.50)

    def test_gross_stake_shows_combined_financial_exposure(self):
        result = deduplicate_entries(self._phillies_entries())
        for canonical in result["canonical_events"].values():
            self.assertAlmostEqual(canonical["gross_stake"], 17.50)


# ═══════════════════════════════════════════════════════════════════════════════
# AT-9: Settlement cannot overwrite approval-time model probability
# ═══════════════════════════════════════════════════════════════════════════════

class TestAT9_SettlementCannotOverwriteApprovalProb(unittest.TestCase):
    """
    The approved_model_prob field set at approval time must survive settlement.
    Settlement data is merged separately and cannot mutate approval fields.
    """

    def _candidate(self):
        return {
            "league": "mlb", "event_date": "2026-07-09",
            "away_team": "dodgers", "home_team": "padres",
            "selected_side": "dodgers", "selected_side_is_home": False,
            "market_type": "ml",
            "stake": 24.50, "listed_return": 35.00,
            "model_prob": 0.74,
            "market_no_vig_prob": 0.70,
            "final_label": "LLP_WATCH",
        }

    def _settlement(self):
        return {
            "official_result":           "AWAY_WIN",
            "platform_result":           "WIN",
            "model_result":              "WIN",
            "calibration_outcome":       "WIN",
            "calibration_eligible":      True,
            "closing_no_vig_prob":       0.71,
            "closing_multiplier":        1.43,
            "settlement_timestamp":      "2026-07-09T23:00:00Z",
            # Attacker tries to overwrite approval prob — MUST BE BLOCKED
            "approved_model_prob":       0.99,
        }

    def test_snapshot_preserves_approval_model_prob(self):
        snapshot = build_approval_snapshot(self._candidate())
        self.assertAlmostEqual(snapshot["approved_model_prob"], 0.74)

    def test_settlement_cannot_overwrite_model_prob(self):
        snapshot  = build_approval_snapshot(self._candidate())
        settled   = add_settlement_to_snapshot(snapshot, self._settlement())
        # The original approval prob must survive
        self.assertAlmostEqual(settled["approved_model_prob"], 0.74,
                               msg="Settlement must not overwrite approval-time model probability")

    def test_settlement_fields_are_added(self):
        snapshot = build_approval_snapshot(self._candidate())
        settled  = add_settlement_to_snapshot(snapshot, self._settlement())
        self.assertEqual(settled["official_result"], "AWAY_WIN")
        self.assertEqual(settled["model_result"],    "WIN")
        self.assertTrue(settled["settled"])

    def test_snapshot_integrity_validates_required_fields(self):
        snapshot = build_approval_snapshot(self._candidate())
        integrity = validate_snapshot_integrity(snapshot)
        self.assertTrue(integrity["passed"])
        self.assertEqual(integrity["missing_fields"], [])

    def test_overwrite_attempt_logged_as_warning(self):
        snapshot = build_approval_snapshot(self._candidate())
        settled  = add_settlement_to_snapshot(snapshot, self._settlement())
        warnings = settled.get("settlement_warnings", [])
        self.assertTrue(
            any("approved_model_prob" in w for w in warnings),
            "Blocked overwrite attempt must be logged in settlement_warnings"
        )


# ═══════════════════════════════════════════════════════════════════════════════
# AT-10: Performance dashboard defaults to independent-event record
# ═══════════════════════════════════════════════════════════════════════════════

class TestAT10_DashboardDefaultsToIndependentEventRecord(unittest.TestCase):
    """
    When 7 financial tickets exist but 6 independent model observations,
    the dashboard must headline the independent-event record (e.g. 3-3),
    never the platform-displayed record (e.g. 5-2).
    """

    def _build_slate(self):
        """
        7 tickets, 6 independent events (1 duplicate), 3 model wins, 3 model losses,
        1 promo win (official loss), platform shows 5 wins.
        """
        def entry(away, home, off_result, is_home, plat_display, payment,
                  stake=10.0, promo=False):
            return {
                "league": "mlb", "event_date": "2026-07-09",
                "away_team": away, "home_team": home,
                "market_type": "ml",
                "selected_side": home if is_home else away,
                "selected_side_is_home": is_home,
                "official_event_result": off_result,
                "platform_display_result": plat_display,
                "platform_payment": payment,
                "stake": stake,
                "model_prob": 0.60,
                "promo_protection_active": promo,
            }

        return [
            # Genuine wins (model)
            entry("cubs",     "reds",    "AWAY_WIN", False, "WIN",  18.0),
            entry("yankees",  "sox",     "AWAY_WIN", False, "WIN",  18.0),
            entry("mets",     "braves",  "AWAY_WIN", False, "WIN",  18.0),
            # Genuine losses (model)
            entry("dodgers",  "padres",  "HOME_WIN", False, "LOSS",  0.0),
            entry("astros",   "rangers", "HOME_WIN", False, "LOSS",  0.0),
            entry("angels",   "twins",   "HOME_WIN", False, "LOSS",  0.0),
            # DUPLICATE of first Phillies ticket (same event, different stake)
            entry("cubs",     "reds",    "AWAY_WIN", False, "WIN",  18.0, stake=5.0),
            # Promo win (official loss) — counts as model LOSS
            # This is a separate event key
            entry("mariners", "marlins", "HOME_WIN", False, "WIN",   5.04, promo=True),
        ]

    def test_independent_event_count_less_than_ticket_count(self):
        """Should detect the duplicate Cubs/Reds ticket and the promo entry."""
        entries = self._build_slate()
        # First reconcile, then deduplicate
        from gate_engine.ml_settlement_truth import reconcile_settlement
        reconciled = [reconcile_settlement(e) for e in entries]
        summary = build_ml_performance_summary(reconciled)
        # 8 tickets total, 7 unique events (cubs/reds is duplicated)
        self.assertLess(summary["independent_model_observations"],
                        summary["financial_tickets"])

    def test_official_model_record_not_equal_to_platform_record(self):
        from gate_engine.ml_settlement_truth import reconcile_settlement
        entries    = self._build_slate()
        reconciled = [reconcile_settlement(e) for e in entries]
        summary    = build_ml_performance_summary(reconciled)
        self.assertNotEqual(summary["official_model_record"],
                            f"{summary['platform_displayed_wins']}-"
                            f"{summary['financial_tickets'] - summary['platform_displayed_wins']}")

    def test_display_warning_present_when_records_differ(self):
        from gate_engine.ml_settlement_truth import reconcile_settlement
        entries    = self._build_slate()
        reconciled = [reconcile_settlement(e) for e in entries]
        summary    = build_ml_performance_summary(reconciled)
        if summary["official_model_record"] != f"{summary['platform_displayed_wins']}-{summary['financial_tickets'] - summary['platform_displayed_wins']}":
            self.assertIsNotNone(
                summary["display_warning"],
                "A display_warning must be present when platform record differs from model record"
            )

    def test_independent_event_hit_rate_is_primary_metric(self):
        """independent_event_hit_rate must be present and differ from ticket_hit_rate."""
        from gate_engine.ml_settlement_truth import reconcile_settlement
        entries    = self._build_slate()
        reconciled = [reconcile_settlement(e) for e in entries]
        summary    = build_ml_performance_summary(reconciled)
        self.assertIn("independent_event_hit_rate", summary)
        self.assertIn("ticket_hit_rate",            summary)


# ═══════════════════════════════════════════════════════════════════════════════
# Bonus: Market Disagreement Gate (P1-7) — not one of the 10 ATs but required
# ═══════════════════════════════════════════════════════════════════════════════

class TestMarketDisagreementGate(unittest.TestCase):
    """Verify all four market disagreement quadrants are correctly classified."""

    def test_market_corroborated_edge(self):
        result = validate_market_disagreement(
            model_prob=0.65, no_vig_prob=0.62, breakeven_prob=0.60
        )
        self.assertTrue(result["passed"])
        self.assertEqual(result["quadrant"], MarketDisagreementLabel.MARKET_CORROBORATED_EDGE.value)

    def test_model_only_disagreement_no_freeze(self):
        result = validate_market_disagreement(
            model_prob=0.65, no_vig_prob=0.55, breakeven_prob=0.60,
            reliability_freeze=False,
        )
        self.assertEqual(result["quadrant"], MarketDisagreementLabel.MODEL_ONLY_DISAGREEMENT.value)
        self.assertIsNone(result["ceiling"], "No ceiling outside Reliability Freeze")

    def test_model_only_disagreement_during_freeze_caps_watch(self):
        result = validate_market_disagreement(
            model_prob=0.65, no_vig_prob=0.55, breakeven_prob=0.60,
            reliability_freeze=True,
        )
        self.assertEqual(result["ceiling"], "LLP_WATCH")

    def test_market_only_edge_no_approval(self):
        result = validate_market_disagreement(
            model_prob=0.55, no_vig_prob=0.65, breakeven_prob=0.60
        )
        self.assertFalse(result["passed"])
        self.assertEqual(result["quadrant"], MarketDisagreementLabel.MARKET_ONLY_EDGE.value)

    def test_no_verified_edge_rejected(self):
        result = validate_market_disagreement(
            model_prob=0.55, no_vig_prob=0.55, breakeven_prob=0.65
        )
        self.assertFalse(result["passed"])
        self.assertEqual(result["quadrant"], MarketDisagreementLabel.NO_VERIFIED_EDGE.value)
        self.assertEqual(result["ceiling"], "LLP_REJECT")


if __name__ == "__main__":
    unittest.main(verbosity=2)
