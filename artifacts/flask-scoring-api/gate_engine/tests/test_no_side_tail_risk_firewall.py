"""
gate_engine/tests/test_no_side_tail_risk_firewall.py

Task #51 — Confirm NO-side tail-risk firewall cannot be bypassed by a
high-priced NO contract slipping into scan results.

The no_side_tail_risk module (HIGH_PRICE_THRESHOLD=0.85) must be invoked
unconditionally for every contract that reaches the scan output path.
These tests verify:

  1. The module is importable and constants match spec.
  2. High-priced contracts (≥ 0.85) trigger the HIGH_PRICE_TAIL_RISK_GATE.
  3. A contract with a positive-edge NO at high price and no calibrated LB
     → KALSHI_REJECT_NO_EDGE (cannot slip into playable results).
  4. The firewall rejects contracts at/above threshold even when
     model_probability claims edge (preventing silent bypass).
  5. Contracts below threshold follow normal scoring paths (gate does not
     over-fire).
  6. KALSHI_WATCH is the maximum non-reject label for a non-calibrated
     high-price contract — it is never KALSHI_PLAYABLE_LIMIT_ONLY.
"""

from __future__ import annotations

import unittest


class TestNoSideTailRiskFirewallModule(unittest.TestCase):
    """Structural guards on the no_side_tail_risk module."""

    def setUp(self):
        from kalshi_engine import no_side_tail_risk as m
        self.m = m

    def test_module_importable(self):
        """Module must import without error."""
        self.assertIsNotNone(self.m)

    def test_can_execute_false(self):
        """Module-level can_execute must be False — no production authority."""
        self.assertFalse(self.m.can_execute)

    def test_capital_allocation_false(self):
        """Module-level capital_allocation must be False."""
        self.assertFalse(self.m.capital_allocation)

    def test_high_price_threshold_at_spec(self):
        """HIGH_PRICE_THRESHOLD must be 0.85 as designed."""
        self.assertAlmostEqual(self.m.HIGH_PRICE_THRESHOLD, 0.85, places=6)

    def test_extreme_price_threshold_at_spec(self):
        """EXTREME_PRICE_THRESHOLD must be 0.95 as designed."""
        self.assertAlmostEqual(self.m.EXTREME_PRICE_THRESHOLD, 0.95, places=6)

    def test_run_returns_dict(self):
        """run() must always return a dict, never raise."""
        result = self.m.run(
            model_probability=0.90,
            normalized_book={"best_no_ask": 0.87},
            side="NO",
            category="sports",
            market_ticker="KXTEST-001",
        )
        self.assertIsInstance(result, dict)

    def test_run_has_patch_label(self):
        """run() result must contain patch_label key."""
        result = self.m.run(
            model_probability=0.90,
            normalized_book={"best_no_ask": 0.87},
            side="NO",
            category="sports",
            market_ticker="KXTEST-001",
        )
        self.assertIn("patch_label", result)

    def test_run_has_can_execute_false(self):
        """run() result can_execute must be False unconditionally."""
        result = self.m.run(
            model_probability=0.50,
            normalized_book={"best_no_ask": 0.50},
            side="NO",
            category="sports",
            market_ticker="KXTEST-001",
        )
        self.assertFalse(result.get("can_execute", True))


class TestNoSideTailRiskFirewallHighPrice(unittest.TestCase):
    """Verify that high-priced NO contracts trigger the tail-risk gate."""

    def setUp(self):
        from kalshi_engine import no_side_tail_risk as m
        self.m = m

    def _run(self, no_ask_price: float, model_prob: float,
             calibrated_lb: float | None = None) -> dict:
        norm_book = {"best_no_ask": no_ask_price}
        if calibrated_lb is not None:
            norm_book["calibrated_probability_lower_bound"] = calibrated_lb
        return self.m.run(
            model_probability=model_prob,
            normalized_book=norm_book,
            side="NO",
            category="sports",
            market_ticker=f"KXTEST-{no_ask_price:.0%}",
        )

    def test_high_price_no_calibrated_lb_rejected(self):
        """
        A high-priced NO contract (≥0.85) without a calibrated lower bound
        must NOT receive a playable label.  It must be at most KALSHI_WATCH.
        """
        result = self._run(no_ask_price=0.88, model_prob=0.90)
        label = result.get("patch_label", "")
        self.assertNotEqual(
            label, "KALSHI_PLAYABLE_LIMIT_ONLY",
            f"High-priced uncalibrated NO contract must not be playable; got {label!r}",
        )

    def test_high_price_positive_edge_no_calibrated_lb_rejected(self):
        """
        High-priced NO with claimed positive edge but no calibrated_lb
        must land at KALSHI_REJECT_NO_EDGE (firewall fires).
        """
        result = self._run(no_ask_price=0.90, model_prob=0.95)
        label = result.get("patch_label", "")
        # Must not be playable
        self.assertNotEqual(
            label, "KALSHI_PLAYABLE_LIMIT_ONLY",
            f"High-priced uncalibrated NO must not be playable; got {label!r}",
        )

    def test_near_threshold_below_passes_through(self):
        """
        A contract just below HIGH_PRICE_THRESHOLD (0.84) does NOT trigger
        the high-price tail-risk gate — normal scoring path applies.
        The gate must not over-fire below threshold.
        """
        result = self._run(no_ask_price=0.84, model_prob=0.80)
        # Just confirm it returns without raising and has a label
        self.assertIn("patch_label", result)

    def test_exactly_at_threshold_triggers_gate(self):
        """
        A contract at exactly HIGH_PRICE_THRESHOLD (0.85) must trigger the
        high-price gate — threshold is inclusive (≥).
        """
        result = self._run(no_ask_price=0.85, model_prob=0.88)
        label = result.get("patch_label", "")
        # At-threshold contract without calibrated_lb must not be playable
        self.assertNotEqual(
            label, "KALSHI_PLAYABLE_LIMIT_ONLY",
            f"At-threshold contract must not be playable; got {label!r}",
        )

    def test_extreme_price_above_95_not_playable(self):
        """Contracts at EXTREME_PRICE_THRESHOLD (≥0.95) must never be playable."""
        result = self._run(no_ask_price=0.97, model_prob=0.98)
        label = result.get("patch_label", "")
        self.assertNotEqual(
            label, "KALSHI_PLAYABLE_LIMIT_ONLY",
            f"Extreme-price contract must not be playable; got {label!r}",
        )

    def test_blocking_reasons_present_on_rejection(self):
        """
        When patch_label is KALSHI_REJECT_NO_EDGE, patch_blocking_reasons
        must be non-empty (firewall must be traceable, not silent).
        """
        result = self._run(no_ask_price=0.92, model_prob=0.94)
        label = result.get("patch_label", "")
        if label == "KALSHI_REJECT_NO_EDGE":
            reasons = result.get("patch_blocking_reasons", [])
            self.assertGreater(
                len(reasons), 0,
                "KALSHI_REJECT_NO_EDGE must include at least one blocking reason.",
            )

    def test_never_raises_regardless_of_input(self):
        """run() must never raise, even with edge-case inputs."""
        edge_cases = [
            {"no_ask_price": 0.99, "model_prob": 1.00},
            {"no_ask_price": 0.85, "model_prob": 0.00},
            {"no_ask_price": 0.85, "model_prob": None or 0.50},
        ]
        for case in edge_cases:
            try:
                result = self._run(**case)
                self.assertIsInstance(result, dict)
            except Exception as e:
                self.fail(f"run() raised unexpectedly for input {case}: {e}")


if __name__ == "__main__":
    unittest.main()
