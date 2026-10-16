"""Unit tests for research-only sportsbook parlay economics."""
import unittest
from decimal import Decimal

from parlay_cashout_audit import CAN_EXECUTE, american_decimal, audit_parlay


class ParlayCashoutAuditTests(unittest.TestCase):
    def test_user_example_arithmetic(self):
        x = audit_parlay(stake="794.00", ticket_american_odds=102139,
                         cashout="374028.51", all_other_legs_won=True)
        self.assertEqual(x["gross_full_return"], "811777.66")
        self.assertEqual(x["net_full_profit"], "810983.66")
        self.assertEqual(x["net_cashout_profit"], "373234.51")
        self.assertIsNone(x["conditional_remaining_leg_break_even"])
        self.assertAlmostEqual(float(x["hypothetical_single_remaining_leg_break_even"]),
                               0.460752406, places=6)
        self.assertEqual(x["conditional_evidence_status"], "SCENARIO_ONLY_SOURCE_UNVERIFIED")
        self.assertIsNone(x["governed_joint_probability"])
        self.assertFalse(x["can_execute"])

    def test_price_conversion(self):
        self.assertEqual(american_decimal(265), Decimal("3.65"))
        self.assertEqual(american_decimal(-185), Decimal(1) + Decimal(100) / Decimal(185))
        for bad in (0, 99, -99, -1):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                american_decimal(bad)

    def test_obscured_leg_prices_are_not_invented(self):
        x = audit_parlay(stake=794, ticket_american_odds=102139,
                         cashout=374028.51, leg_american_odds=[265, -185, None])
        self.assertEqual(x["book_price_reconciliation"], "PRICE_EVIDENCE_INCOMPLETE")
        self.assertNotIn("leg_price_product", x)
        self.assertIsNone(x["conditional_remaining_leg_break_even"])

    def test_price_mismatch_requires_evidence_not_fraud_claim(self):
        x = audit_parlay(stake=10, ticket_american_odds=500,
                         cashout=20, leg_american_odds=[100, 100])
        self.assertEqual(x["book_price_reconciliation"],
                         "MULTIPLIER_MISMATCH_REQUIRES_SOURCE_AUDIT")

    def test_more_than_one_remaining_must_not_use_single_leg_break_even(self):
        x = audit_parlay(stake=10, ticket_american_odds=500,
                         cashout=20, all_other_legs_won=True, unresolved_legs=2)
        self.assertIsNone(x["conditional_remaining_leg_break_even"])

    def test_invalid_stakes_fail_closed(self):
        for bad in ("NaN", "Infinity", "-1", "not a number"):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                audit_parlay(stake=bad, ticket_american_odds=500, cashout=10)

    def test_authentication_gate_allows_only_exact_6_win_1_pending(self):
        x = audit_parlay(stake="794.00", ticket_american_odds=102139,
                         cashout="374028.51", leg_results=["WIN"] * 6 + ["PENDING"],
                         externally_verified_receipt=True,
                         authenticated_receipt_ref="authenticated:test-case-only")
        self.assertAlmostEqual(float(x["conditional_remaining_leg_break_even"]),
                               0.460752406, places=6)
        self.assertEqual(x["conditional_evidence_status"],
                         "EXTERNALLY_VERIFIED_RECEIPT_ATTESTED")
        self.assertIsNone(x["governed_joint_probability"])

    def test_without_attestation_outcomes_remain_scenarios(self):
        x = audit_parlay(stake=794, ticket_american_odds=102139,
                         cashout=374028.51, leg_results=["WIN"] * 6 + ["PENDING"])
        self.assertIsNone(x["conditional_remaining_leg_break_even"])
        self.assertEqual(x["conditional_evidence_status"],
                         "SCENARIO_ONLY_SOURCE_UNVERIFIED")

    def test_void_or_loss_must_not_generate_conditional_threshold(self):
        for bad in ("VOID", "LOSS", "PUSH"):
            with self.subTest(bad=bad):
                x = audit_parlay(stake=10, ticket_american_odds=500,
                                 cashout=20, leg_results=["WIN"] * 5 + [bad, "PENDING"],
                                 externally_verified_receipt=True,
                                 authenticated_receipt_ref="fixture")
                self.assertIsNone(x["conditional_remaining_leg_break_even"])
                self.assertEqual(x["conditional_evidence_status"],
                                 "NONWIN_VOID_OR_MULTIPLE_UNRESOLVED")

    def test_verified_flag_without_receipt_ref_fails(self):
        with self.assertRaises(ValueError):
            audit_parlay(stake=10, ticket_american_odds=500,
                         cashout=20, externally_verified_receipt=True)

    def test_invalid_status_fails(self):
        with self.assertRaises(ValueError):
            audit_parlay(stake=10, ticket_american_odds=500,
                         cashout=20, leg_results=["WIN", "GUESS"])

    def test_excess_cashout_offer_flagged_for_review(self):
        x = audit_parlay(stake=10, ticket_american_odds=100,
                         cashout=21, leg_results=["WIN", "PENDING"])
        self.assertEqual(x["cashout_quote_integrity"],
                         "OFFER_EXCEEDS_FULL_PAYOUT_REQUIRES_AUDIT")

    def test_one_leg_unverified_does_not_become_verified_from_bool(self):
        x = audit_parlay(stake=10, ticket_american_odds=500, cashout=20,
                         all_other_legs_won=True, unresolved_legs=1)
        self.assertIsNone(x["conditional_remaining_leg_break_even"])
        self.assertIsNotNone(x["hypothetical_single_remaining_leg_break_even"])

    def test_never_execute(self):
        self.assertIs(CAN_EXECUTE, False)


if __name__ == "__main__":
    unittest.main()
