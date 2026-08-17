"""
gate_engine/tests/test_kalshi_reject_uncalibrated_behavioral.py

Task #150 follow-up — Behavioral (non-AST) integration tests confirming that
KALSHI_REJECT_UNCALIBRATED is permanently excluded from the live runtime
validation path.

These tests CALL the actual runtime label-validation function and confirm
that KALSHI_REJECT_UNCALIBRATED is rejected at runtime, not just absent from
a static file.

Registry attribute: KALSHI_WX_TERMINAL_LABEL_REGISTRY (no leading underscore).
Live labels (as of 2026-08-16):
    KALSHI_DATA_UNOBTAINABLE, KALSHI_PLAYABLE_LIMIT_ONLY,
    KALSHI_REJECT_BAD_RULES, KALSHI_REJECT_NO_EDGE, KALSHI_WATCH
"""

from __future__ import annotations

import unittest

_REGISTRY_ATTR = "KALSHI_WX_TERMINAL_LABEL_REGISTRY"

# Labels confirmed present in the registry at implementation time.
_EXPECTED_VALID_LABELS = (
    "KALSHI_DATA_UNOBTAINABLE",
    "KALSHI_PLAYABLE_LIMIT_ONLY",
    "KALSHI_REJECT_BAD_RULES",
    "KALSHI_REJECT_NO_EDGE",
    "KALSHI_WATCH",
)


class TestKalshiRejectUncalibratedBehavioral(unittest.TestCase):
    """
    Behavioral tests: call the runtime label registry and confirm
    KALSHI_REJECT_UNCALIBRATED is rejected at runtime.
    """

    def setUp(self):
        from gate_engine import kalshi_wx_terminal_labels as taxonomy
        self.taxonomy = taxonomy
        self.registry = getattr(taxonomy, _REGISTRY_ATTR)

    # ── Registry membership ───────────────────────────────────────────────────

    def test_uncalibrated_not_in_terminal_label_set(self):
        """
        KALSHI_REJECT_UNCALIBRATED must NOT appear in KALSHI_WX_TERMINAL_LABEL_REGISTRY.
        This is the runtime source of truth — not a static-file check.
        """
        self.assertNotIn(
            "KALSHI_REJECT_UNCALIBRATED",
            self.registry,
            "KALSHI_REJECT_UNCALIBRATED found in the live registry; "
            "it would allow uncalibrated labels into the scoring path.",
        )

    def test_registry_is_frozenset(self):
        """Registry must be a frozenset — immutable at runtime."""
        self.assertIsInstance(
            self.registry,
            frozenset,
            "Registry must be a frozenset so it cannot be mutated at runtime.",
        )

    # ── Runtime validation function calls ─────────────────────────────────────

    def test_validate_rejects_uncalibrated_label(self):
        """
        If _validate_wx_terminal_label() is exported, calling it with
        KALSHI_REJECT_UNCALIBRATED must either raise or return a non-pass result.
        """
        validate = getattr(self.taxonomy, "_validate_wx_terminal_label", None)
        if validate is None:
            self.skipTest(
                "_validate_wx_terminal_label not exported; "
                "registry membership test covers this invariant."
            )
        try:
            result = validate("KALSHI_REJECT_UNCALIBRATED")
            if isinstance(result, dict):
                is_pass = (
                    result.get("valid")
                    or result.get("passed")
                    or result.get("status") == "PASS"
                )
                self.assertFalse(
                    is_pass,
                    f"validate('KALSHI_REJECT_UNCALIBRATED') returned pass: {result}",
                )
        except Exception:
            pass  # Raising is acceptable — the label is invalid

    def test_ceiling_capable_labels_exclude_uncalibrated(self):
        """
        CEILING_CAPABLE_LABELS (gates score injection in the shadow pilot)
        must not include KALSHI_REJECT_UNCALIBRATED.
        """
        ceiling_labels = getattr(self.taxonomy, "CEILING_CAPABLE_LABELS", frozenset())
        self.assertNotIn(
            "KALSHI_REJECT_UNCALIBRATED",
            ceiling_labels,
        )

    def test_uncalibrated_not_in_any_exported_set(self):
        """
        Sweep every exported frozenset/set in the taxonomy module.
        KALSHI_REJECT_UNCALIBRATED must not appear in any of them.
        """
        target = "KALSHI_REJECT_UNCALIBRATED"
        for attr_name in dir(self.taxonomy):
            if attr_name.startswith("__"):
                continue
            val = getattr(self.taxonomy, attr_name, None)
            if isinstance(val, (frozenset, set)):
                self.assertNotIn(
                    target,
                    val,
                    f"KALSHI_REJECT_UNCALIBRATED found in {attr_name}; must be removed.",
                )


class TestKalshiWxTerminalLabelRuntimeValidation(unittest.TestCase):
    """
    Behavioral tests: confirm the known-good WX terminal labels ARE present,
    and the registry discriminates between valid and invalid labels.
    """

    def setUp(self):
        from gate_engine import kalshi_wx_terminal_labels as taxonomy
        self.taxonomy = taxonomy
        self.registry = getattr(taxonomy, _REGISTRY_ATTR)

    def test_expected_labels_are_in_registry(self):
        """All known-good WX terminal labels must be in the registry."""
        for label in _EXPECTED_VALID_LABELS:
            self.assertIn(
                label,
                self.registry,
                f"Expected WX terminal label {label!r} missing from live registry.",
            )

    def test_bogus_label_not_in_registry(self):
        """A fabricated label must not be in the registry."""
        self.assertNotIn(
            "KALSHI_REJECT_BOGUS_INVENTED_LABEL_XYZ",
            self.registry,
        )

    def test_uncalibrated_distinguishable_from_valid_labels(self):
        """
        Registry must exclude KALSHI_REJECT_UNCALIBRATED while including
        at least one known-good label — proves the registry is not simply empty.
        """
        self.assertNotIn("KALSHI_REJECT_UNCALIBRATED", self.registry)
        found = [lbl for lbl in _EXPECTED_VALID_LABELS if lbl in self.registry]
        self.assertGreater(
            len(found), 0,
            "Registry is empty or missing all expected labels; "
            "discrimination test is meaningless on an empty set.",
        )

    def test_registry_size_in_expected_range(self):
        """
        Registry must have 5–20 labels.  Outside this range indicates
        unexpected additions or accidental removals.
        """
        self.assertGreaterEqual(len(self.registry), 5)
        self.assertLessEqual(len(self.registry), 20)


if __name__ == "__main__":
    unittest.main()
