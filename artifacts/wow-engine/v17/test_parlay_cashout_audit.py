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
        self.assertAlmostEqual(float(x["conditional_remaining_leg_break_even"]),
                               0.460752406, places=6)
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

    def test_never_execute(self):
        self.assertIs(CAN_EXECUTE, False)


if __name__ == "__main__":
    unittest.main()
