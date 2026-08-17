"""
gate_engine/tests/test_kalshi_reject_uncalibrated_behavioral.py

Task #150 follow-up — Behavioral (non-AST) integration tests confirming that
KALSHI_REJECT_UNCALIBRATED is permanently excluded from the live runtime
validation path.

WOW-PATCH-2026-08-16-AUDIT fix (4): these tests call the ACTUAL runtime
validation function validate_wx_terminal_label() — no test may be skipped.

Registry attribute: KALSHI_WX_TERMINAL_LABEL_REGISTRY (no leading underscore).
Public validator:   validate_wx_terminal_label(label) → bool
Live labels (as of 2026-08-16):
    KALSHI_DATA_UNOBTAINABLE, KALSHI_PLAYABLE_LIMIT_ONLY,
    KALSHI_REJECT_BAD_RULES, KALSHI_REJECT_NO_EDGE, KALSHI_WATCH
"""

from __future__ import annotations

import unittest

_REGISTRY_ATTR = "KALSHI_WX_TERMINAL_LABEL_REGISTRY"

_EXPECTED_VALID_LABELS = (
    "KALSHI_DATA_UNOBTAINABLE",
    "KALSHI_PLAYABLE_LIMIT_ONLY",
    "KALSHI_REJECT_BAD_RULES",
    "KALSHI_REJECT_NO_EDGE",
    "KALSHI_WATCH",
)


class TestKalshiRejectUncalibratedEndToEnd(unittest.TestCase):
    """
    End-to-end behavioral tests: call validate_wx_terminal_label() and prove
    KALSHI_REJECT_UNCALIBRATED is rejected at runtime. No test may be skipped.
    """

    def setUp(self):
        from gate_engine.kalshi_wx_terminal_labels import (
            KALSHI_WX_TERMINAL_LABEL_REGISTRY,
            validate_wx_terminal_label,
        )
        self.registry = KALSHI_WX_TERMINAL_LABEL_REGISTRY
        self.validate = validate_wx_terminal_label

    def test_validate_rejects_uncalibrated_label(self):
        """
        validate_wx_terminal_label('KALSHI_REJECT_UNCALIBRATED') must return
        False.  This is the runtime rejection path — MUST NOT be skipped.
        """
        result = self.validate("KALSHI_REJECT_UNCALIBRATED")
        self.assertFalse(
            result,
            "validate_wx_terminal_label('KALSHI_REJECT_UNCALIBRATED') returned True; "
            "the label must be rejected by the runtime validator.",
        )

    def test_validate_accepts_known_good_label(self):
        """Runtime validator must return True for a confirmed-reachable label."""
        self.assertTrue(
            self.validate("KALSHI_WATCH"),
            "validate_wx_terminal_label('KALSHI_WATCH') returned False; "
            "known-good labels must be accepted.",
        )

    def test_validate_accepts_all_expected_labels(self):
        """All five known-good WX terminal labels must pass the runtime validator."""
        for label in _EXPECTED_VALID_LABELS:
            with self.subTest(label=label):
                self.assertTrue(
                    self.validate(label),
                    f"validate_wx_terminal_label({label!r}) returned False; "
                    f"this label must be accepted.",
                )

    def test_validate_rejects_bogus_label(self):
        """A fabricated label must be rejected by the runtime validator."""
        self.assertFalse(self.validate("KALSHI_REJECT_BOGUS_INVENTED_LABEL_XYZ"))

    def test_validate_returns_bool(self):
        """Validator must return a proper bool for both valid and invalid inputs."""
        good = self.validate("KALSHI_WATCH")
        bad  = self.validate("KALSHI_REJECT_UNCALIBRATED")
        self.assertIsInstance(good, bool)
        self.assertIsInstance(bad, bool)

    def test_validate_is_consistent_with_registry_membership(self):
        """
        validate_wx_terminal_label(lbl) must agree with (lbl in registry)
        for every expected label and for KALSHI_REJECT_UNCALIBRATED.
        """
        targets = list(_EXPECTED_VALID_LABELS) + ["KALSHI_REJECT_UNCALIBRATED"]
        for label in targets:
            with self.subTest(label=label):
                self.assertEqual(
                    self.validate(label),
                    label in self.registry,
                    f"validate_wx_terminal_label({label!r}) disagrees with registry membership.",
                )


class TestKalshiRejectUncalibratedBehavioral(unittest.TestCase):
    """Registry-level behavioral tests (complement to the end-to-end set above)."""

    def setUp(self):
        from gate_engine import kalshi_wx_terminal_labels as taxonomy
        self.taxonomy = taxonomy
        self.registry = getattr(taxonomy, _REGISTRY_ATTR)

    def test_uncalibrated_not_in_terminal_label_set(self):
        """KALSHI_REJECT_UNCALIBRATED must NOT appear in the live registry."""
        self.assertNotIn("KALSHI_REJECT_UNCALIBRATED", self.registry)

    def test_registry_is_frozenset(self):
        """Registry must be a frozenset — immutable at runtime."""
        self.assertIsInstance(self.registry, frozenset)

    def test_ceiling_capable_labels_exclude_uncalibrated(self):
        """CEILING_CAPABLE_LABELS must not include KALSHI_REJECT_UNCALIBRATED."""
        ceiling_labels = getattr(self.taxonomy, "CEILING_CAPABLE_LABELS", frozenset())
        self.assertNotIn("KALSHI_REJECT_UNCALIBRATED", ceiling_labels)

    def test_uncalibrated_not_in_any_exported_set(self):
        """Sweep every exported frozenset/set — uncalibrated must not appear."""
        target = "KALSHI_REJECT_UNCALIBRATED"
        for attr_name in dir(self.taxonomy):
            if attr_name.startswith("__"):
                continue
            val = getattr(self.taxonomy, attr_name, None)
            if isinstance(val, (frozenset, set)):
                self.assertNotIn(
                    target, val,
                    f"KALSHI_REJECT_UNCALIBRATED found in {attr_name}.",
                )


class TestKalshiWxTerminalLabelRuntimeValidation(unittest.TestCase):
    """Discriminability tests: registry must accept valid labels and reject invalid ones."""

    def setUp(self):
        from gate_engine import kalshi_wx_terminal_labels as taxonomy
        self.taxonomy = taxonomy
        self.registry = getattr(taxonomy, _REGISTRY_ATTR)

    def test_expected_labels_are_in_registry(self):
        for label in _EXPECTED_VALID_LABELS:
            with self.subTest(label=label):
                self.assertIn(label, self.registry)

    def test_bogus_label_not_in_registry(self):
        self.assertNotIn("KALSHI_REJECT_BOGUS_INVENTED_LABEL_XYZ", self.registry)

    def test_uncalibrated_distinguishable_from_valid_labels(self):
        self.assertNotIn("KALSHI_REJECT_UNCALIBRATED", self.registry)
        found = [lbl for lbl in _EXPECTED_VALID_LABELS if lbl in self.registry]
        self.assertGreater(len(found), 0, "Registry is empty; discrimination is meaningless.")

    def test_registry_size_in_expected_range(self):
        self.assertGreaterEqual(len(self.registry), 5)
        self.assertLessEqual(len(self.registry), 20)


if __name__ == "__main__":
    unittest.main()
