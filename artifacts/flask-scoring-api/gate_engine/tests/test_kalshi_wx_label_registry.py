"""
gate_engine/tests/test_kalshi_wx_label_registry.py

Task #150 — Guard: Prevent KALSHI_REJECT_UNCALIBRATED from silently
re-appearing in the WX terminal label registry without a real code path.

The label was removed 2026-08-09 because no route handler ever assigns
weather_label="WEATHER_REJECT_UNCALIBRATED", making the branch that produced
it permanently dead code.  These tests make that removal permanent and
machine-auditable.

Re-adding KALSHI_REJECT_UNCALIBRATED to KALSHI_WX_TERMINAL_LABEL_REGISTRY
requires:
  1. A live return statement in _weather_terminal_label_v2() for
     weather_label="WEATHER_REJECT_UNCALIBRATED" (not just a docstring).
  2. A matching assignment of weather_label="WEATHER_REJECT_UNCALIBRATED"
     in both /kalshi/weather route handlers.
  3. An update to this test suite (which will fail if the label appears
     in the registry without re-reading the docstring above).
"""

from __future__ import annotations

import unittest

from gate_engine.kalshi_wx_terminal_labels import KALSHI_WX_TERMINAL_LABEL_REGISTRY


class TestKalshiWxLabelRegistryGuard(unittest.TestCase):
    """Machine-auditable guard for the KALSHI_WX_TERMINAL_LABEL_REGISTRY."""

    # -----------------------------------------------------------------------
    # Core guard: the removed label must NOT be in the registry
    # -----------------------------------------------------------------------

    def test_kalshi_reject_uncalibrated_not_in_registry(self):
        """
        KALSHI_REJECT_UNCALIBRATED must never appear in KALSHI_WX_TERMINAL_LABEL_REGISTRY.

        If this test fails, a new entry was added without a live code path.
        Before re-adding: (1) confirm the route handler assigns
        weather_label='WEATHER_REJECT_UNCALIBRATED', and (2) add a live
        return branch in _weather_terminal_label_v2() for that value.
        """
        self.assertNotIn(
            "KALSHI_REJECT_UNCALIBRATED",
            KALSHI_WX_TERMINAL_LABEL_REGISTRY,
            "KALSHI_REJECT_UNCALIBRATED was re-added to the WX terminal label "
            "registry without a confirmed live code path.  See task #150 for "
            "the re-add protocol.",
        )

    def test_kalshi_reject_thin_book_not_in_registry(self):
        """
        KALSHI_REJECT_THIN_BOOK must not appear until price-gate work assigns it.
        Paired with the uncalibrated guard per the same exclusion rationale.
        """
        self.assertNotIn(
            "KALSHI_REJECT_THIN_BOOK",
            KALSHI_WX_TERMINAL_LABEL_REGISTRY,
            "KALSHI_REJECT_THIN_BOOK is excluded until price-gate work assigns it.",
        )

    def test_kalshi_reject_fee_drag_not_in_registry(self):
        """KALSHI_REJECT_FEE_DRAG must not appear until price-gate work assigns it."""
        self.assertNotIn(
            "KALSHI_REJECT_FEE_DRAG",
            KALSHI_WX_TERMINAL_LABEL_REGISTRY,
            "KALSHI_REJECT_FEE_DRAG is excluded until price-gate work assigns it.",
        )

    # -----------------------------------------------------------------------
    # Positive guard: the confirmed-reachable labels ARE in the registry
    # -----------------------------------------------------------------------

    def test_confirmed_reachable_labels_in_registry(self):
        """
        All labels with confirmed live code paths must remain in the registry.
        If this test fails, a confirmed label was removed — add back with its
        live code path documentation.
        """
        required = {
            "KALSHI_PLAYABLE_LIMIT_ONLY",
            "KALSHI_WATCH",
            "KALSHI_REJECT_NO_EDGE",
            "KALSHI_REJECT_BAD_RULES",
            "KALSHI_DATA_UNOBTAINABLE",
        }
        for label in required:
            self.assertIn(
                label,
                KALSHI_WX_TERMINAL_LABEL_REGISTRY,
                f"Confirmed-reachable label {label!r} was removed from the registry.",
            )

    def test_registry_is_frozen_set(self):
        """Registry must be a frozenset (immutable, no runtime mutations possible)."""
        self.assertIsInstance(
            KALSHI_WX_TERMINAL_LABEL_REGISTRY,
            frozenset,
            "KALSHI_WX_TERMINAL_LABEL_REGISTRY must remain a frozenset.",
        )

    def test_registry_contains_only_kalshi_prefixed_labels(self):
        """Every label in the registry must start with 'KALSHI_' to prevent scope creep."""
        for label in KALSHI_WX_TERMINAL_LABEL_REGISTRY:
            self.assertTrue(
                label.startswith("KALSHI_"),
                f"Non-KALSHI label found in registry: {label!r}",
            )

    def test_registry_size_unchanged_from_known_count(self):
        """
        Registry size acts as a change-detection tripwire.  Any addition or
        removal will fail this test, requiring an explicit review of the
        label's code-path status before proceeding.

        Current confirmed count: 5 labels (as of 2026-08-09 removal).
        """
        self.assertEqual(
            len(KALSHI_WX_TERMINAL_LABEL_REGISTRY),
            5,
            f"Registry size changed (expected 5, got "
            f"{len(KALSHI_WX_TERMINAL_LABEL_REGISTRY)}).  Review all additions/"
            f"removals against the code-path confirmation protocol in task #150.",
        )

    # -----------------------------------------------------------------------
    # Isolation guard: module is not imported from forbidden paths
    # -----------------------------------------------------------------------

    def test_module_has_no_forbidden_imports_ast(self):
        """
        kalshi_wx_terminal_labels must not import (directly or via star-import)
        gate_engine.command_center, gate_engine.wow_runtime_manifest, or app.

        Uses AST so the check is test-order-independent (sys.modules can be
        polluted by earlier tests in the full suite).
        """
        import ast
        import pathlib

        src = (
            pathlib.Path(__file__).resolve().parent.parent
            / "kalshi_wx_terminal_labels.py"
        )
        self.assertTrue(src.exists(), f"Module not found: {src}")
        tree = ast.parse(src.read_text())

        forbidden_substrings = [
            "command_center",
            "wow_runtime_manifest",
        ]
        for node in ast.walk(tree):
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                module_str = ""
                if isinstance(node, ast.ImportFrom) and node.module:
                    module_str = node.module
                elif isinstance(node, ast.Import):
                    module_str = " ".join(alias.name for alias in node.names)
                for substr in forbidden_substrings:
                    self.assertNotIn(
                        substr,
                        module_str,
                        f"kalshi_wx_terminal_labels.py imports forbidden module "
                        f"containing '{substr}': {module_str!r}",
                    )


if __name__ == "__main__":
    unittest.main()
