"""
tests/test_kalshi_wx_shadow_registry.py
WOW-PATCH-2026-08-08-MULTI-AGENT-KALSHI-WX-SHADOW — Step 7 tests

Validates the taxonomy registry in gate_engine/kalshi_wx_shadow_registry.py.

Test plan
─────────
Section A — Namespace membership (unit)
  A1:  OperationalState.MEMBERS == {"SHADOW_ONLY", "DRY_RUN_ONLY"}
  A2:  ModelReadiness.MEMBERS == {"WEATHER_SCOUT","WEATHER_WATCH","WEATHER_MODEL_READY"}
  A3:  TerminalProjection.kalshi_weather.MEMBERS contains exactly the 5 labels.
  A4:  String constants on each namespace class match their MEMBERS set.

Section B — Single source of truth (no drift from prerequisite patch)
  B1:  TerminalProjection.kalshi_weather.MEMBERS is the SAME OBJECT as
       KALSHI_WX_TERMINAL_LABEL_REGISTRY from gate_engine/kalshi_wx_terminal_labels.py
       (identity check — not just equality).
  B2:  TerminalProjection.kalshi_weather.MEMBERS == the prerequisite registry
       (equality check — belt-and-suspenders against a future object identity break).
  B3:  app.py's inline _KALSHI_WX_TERMINAL_LABEL_REGISTRY is now an import from
       gate_engine/kalshi_wx_terminal_labels — confirmed by verifying the frozenset
       definition is absent from the app.py source text and the import line is present.

Section C — Ceiling-capable subset
  C1:  CEILING_CAPABLE_LABELS equals TerminalProjection.kalshi_weather.MEMBERS exactly.
  C2:  Each of the 6 terminal-projection labels IS in CEILING_CAPABLE_LABELS.
  C3:  Each OperationalState value (SHADOW_ONLY, DRY_RUN_ONLY) is NOT in
       CEILING_CAPABLE_LABELS.
  C4:  Each ModelReadiness value (WEATHER_SCOUT, WEATHER_WATCH, WEATHER_MODEL_READY)
       is NOT in CEILING_CAPABLE_LABELS.
  C5:  An invented string is not in CEILING_CAPABLE_LABELS.
  C6:  CEILING_CAPABLE_LABELS size is exactly 6.

Section D — is_ceiling_capable() function
  D1:  Returns True for every label in TerminalProjection.kalshi_weather.MEMBERS.
  D2:  Returns False for every OperationalState value.
  D3:  Returns False for every ModelReadiness value.
  D4:  Returns False for an invented string.
  D5:  Returns False for KALSHI_REJECT_THIN_BOOK (docstring-only label).
  D6:  Returns False for empty string.

Section E — Namespace disjointness
  E1:  OperationalState.MEMBERS ∩ ModelReadiness.MEMBERS = ∅
  E2:  OperationalState.MEMBERS ∩ CEILING_CAPABLE_LABELS = ∅
  E3:  ModelReadiness.MEMBERS ∩ CEILING_CAPABLE_LABELS = ∅
  E4:  All three MEMBERS sets are pairwise disjoint.

Section F — Isolation: ceiling resolvers do not reference shadow registry symbols
  F1:  gate_engine/wow_runtime_manifest.py does not reference the shadow registry.
  F2:  gate_engine/command_center/cc_labels.py does not reference the shadow registry.
  F3:  gate_engine/command_center/ceiling_resolver.py does not reference the shadow registry.
  Also re-confirm none reference gate_engine/kalshi_wx_terminal_labels (the new shared module).
"""
from __future__ import annotations

import os
import sys
import unittest

# ── path setup ────────────────────────────────────────────────────────────────
_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO not in sys.path:
    sys.path.insert(0, _REPO)

from gate_engine.kalshi_wx_shadow_registry import (
    OperationalState,
    ModelReadiness,
    TerminalProjection,
    CEILING_CAPABLE_LABELS,
    is_ceiling_capable,
)
from gate_engine.kalshi_wx_terminal_labels import KALSHI_WX_TERMINAL_LABEL_REGISTRY

_EXPECTED_TERMINAL_LABELS: frozenset[str] = frozenset({
    "KALSHI_PLAYABLE_LIMIT_ONLY",
    "KALSHI_WATCH",
    "KALSHI_REJECT_NO_EDGE",
    "KALSHI_REJECT_BAD_RULES",
    "KALSHI_DATA_UNOBTAINABLE",
    # KALSHI_REJECT_UNCALIBRATED removed 2026-08-09: no route handler ever assigns
    # weather_label="WEATHER_REJECT_UNCALIBRATED", so the return branch in
    # _weather_terminal_label_v2() was permanently dead code. See test A5 in
    # test_kalshi_wx_terminal_label_failclosed.py for full rationale.
})

_EXPECTED_OPERATIONAL: frozenset[str] = frozenset({"SHADOW_ONLY", "DRY_RUN_ONLY"})
_EXPECTED_MODEL: frozenset[str] = frozenset({
    "WEATHER_SCOUT", "WEATHER_WATCH", "WEATHER_MODEL_READY",
})


# ─────────────────────────────────────────────────────────────────────────────
# Section A — Namespace membership
# ─────────────────────────────────────────────────────────────────────────────

class TestNamespaceMembership(unittest.TestCase):

    def test_A1_operational_state_members(self):
        self.assertEqual(OperationalState.MEMBERS, _EXPECTED_OPERATIONAL)

    def test_A2_model_readiness_members(self):
        self.assertEqual(ModelReadiness.MEMBERS, _EXPECTED_MODEL)

    def test_A3_terminal_projection_kalshi_weather_members(self):
        self.assertEqual(
            TerminalProjection.kalshi_weather.MEMBERS,
            _EXPECTED_TERMINAL_LABELS,
        )

    def test_A4_string_constants_match_members(self):
        """Each class constant must appear in its own MEMBERS set."""
        for val in (OperationalState.SHADOW_ONLY, OperationalState.DRY_RUN_ONLY):
            self.assertIn(val, OperationalState.MEMBERS)
        for val in (
            ModelReadiness.WEATHER_SCOUT,
            ModelReadiness.WEATHER_WATCH,
            ModelReadiness.WEATHER_MODEL_READY,
        ):
            self.assertIn(val, ModelReadiness.MEMBERS)


# ─────────────────────────────────────────────────────────────────────────────
# Section B — Single source of truth / no drift from prerequisite patch
# ─────────────────────────────────────────────────────────────────────────────

class TestSingleSourceOfTruth(unittest.TestCase):

    def test_B1_terminal_projection_IS_prerequisite_registry_object(self):
        """
        The shadow registry must NOT define its own frozenset — it must hold
        the same object imported from gate_engine/kalshi_wx_terminal_labels.py.
        Object identity (`is`) proves no duplication occurred.
        """
        self.assertIs(
            TerminalProjection.kalshi_weather.MEMBERS,
            KALSHI_WX_TERMINAL_LABEL_REGISTRY,
            "TerminalProjection.kalshi_weather.MEMBERS must be the same object "
            "as KALSHI_WX_TERMINAL_LABEL_REGISTRY, not a copy.",
        )

    def test_B2_terminal_projection_equals_prerequisite_registry(self):
        """Belt-and-suspenders equality check."""
        self.assertEqual(
            TerminalProjection.kalshi_weather.MEMBERS,
            KALSHI_WX_TERMINAL_LABEL_REGISTRY,
        )

    def test_B3_app_py_imports_registry_does_not_redefine_it(self):
        """
        After the extraction, app.py must contain the import line and must NOT
        contain an inline frozenset definition for _KALSHI_WX_TERMINAL_LABEL_REGISTRY.
        This test reads app.py as text to verify the file-level contract.
        """
        app_path = os.path.join(_REPO, "app.py")
        with open(app_path, encoding="utf-8") as fh:
            src = fh.read()

        # Import line must be present
        self.assertIn(
            "from gate_engine.kalshi_wx_terminal_labels import",
            src,
            "app.py must import KALSHI_WX_TERMINAL_LABEL_REGISTRY from "
            "gate_engine/kalshi_wx_terminal_labels.py",
        )

        # The old inline frozenset definition block must be gone
        self.assertNotIn(
            '_KALSHI_WX_TERMINAL_LABEL_REGISTRY: frozenset[str] = frozenset({',
            src,
            "app.py must not contain an inline frozenset definition for "
            "_KALSHI_WX_TERMINAL_LABEL_REGISTRY — definition must live only in "
            "gate_engine/kalshi_wx_terminal_labels.py",
        )


# ─────────────────────────────────────────────────────────────────────────────
# Section C — Ceiling-capable subset
# ─────────────────────────────────────────────────────────────────────────────

class TestCeilingCapableSubset(unittest.TestCase):

    def test_C1_ceiling_capable_equals_terminal_projection(self):
        self.assertEqual(
            CEILING_CAPABLE_LABELS,
            TerminalProjection.kalshi_weather.MEMBERS,
        )

    def test_C2_all_6_terminal_labels_in_ceiling_capable(self):
        for label in _EXPECTED_TERMINAL_LABELS:
            with self.subTest(label=label):
                self.assertIn(label, CEILING_CAPABLE_LABELS)

    def test_C3_operational_state_values_not_ceiling_capable(self):
        for label in OperationalState.MEMBERS:
            with self.subTest(label=label):
                self.assertNotIn(
                    label, CEILING_CAPABLE_LABELS,
                    f"OperationalState value {label!r} must not be ceiling-capable",
                )

    def test_C4_model_readiness_values_not_ceiling_capable(self):
        for label in ModelReadiness.MEMBERS:
            with self.subTest(label=label):
                self.assertNotIn(
                    label, CEILING_CAPABLE_LABELS,
                    f"ModelReadiness value {label!r} must not be ceiling-capable",
                )

    def test_C5_invented_string_not_ceiling_capable(self):
        self.assertNotIn("KALSHI_INVENTED_XYZ", CEILING_CAPABLE_LABELS)

    def test_C6_ceiling_capable_size_exactly_5(self):
        """Registry shrank from 6 to 5 when KALSHI_REJECT_UNCALIBRATED was removed."""
        self.assertEqual(len(CEILING_CAPABLE_LABELS), 5)


# ─────────────────────────────────────────────────────────────────────────────
# Section D — is_ceiling_capable()
# ─────────────────────────────────────────────────────────────────────────────

class TestIsCeilingCapable(unittest.TestCase):

    def test_D1_returns_True_for_all_terminal_labels(self):
        for label in _EXPECTED_TERMINAL_LABELS:
            with self.subTest(label=label):
                self.assertTrue(is_ceiling_capable(label))

    def test_D2_returns_False_for_operational_state_values(self):
        for label in OperationalState.MEMBERS:
            with self.subTest(label=label):
                self.assertFalse(
                    is_ceiling_capable(label),
                    f"is_ceiling_capable({label!r}) must be False — "
                    "operational-state values are not ceiling-capable",
                )

    def test_D3_returns_False_for_model_readiness_values(self):
        for label in ModelReadiness.MEMBERS:
            with self.subTest(label=label):
                self.assertFalse(
                    is_ceiling_capable(label),
                    f"is_ceiling_capable({label!r}) must be False — "
                    "model-readiness values are not ceiling-capable",
                )

    def test_D4_returns_False_for_invented_string(self):
        self.assertFalse(is_ceiling_capable("KALSHI_INVENTED_LABEL_XYZ"))

    def test_D5_returns_False_for_KALSHI_REJECT_THIN_BOOK(self):
        """Docstring-only label — excluded from the registry and ceiling-capable."""
        self.assertFalse(is_ceiling_capable("KALSHI_REJECT_THIN_BOOK"))

    def test_D6_returns_False_for_empty_string(self):
        self.assertFalse(is_ceiling_capable(""))


# ─────────────────────────────────────────────────────────────────────────────
# Section E — Namespace disjointness
# ─────────────────────────────────────────────────────────────────────────────

class TestNamespaceDisjointness(unittest.TestCase):

    def test_E1_operational_and_model_readiness_disjoint(self):
        overlap = OperationalState.MEMBERS & ModelReadiness.MEMBERS
        self.assertEqual(overlap, frozenset(),
                         f"OperationalState ∩ ModelReadiness must be empty; got {overlap}")

    def test_E2_operational_and_ceiling_capable_disjoint(self):
        overlap = OperationalState.MEMBERS & CEILING_CAPABLE_LABELS
        self.assertEqual(overlap, frozenset(),
                         f"OperationalState ∩ CEILING_CAPABLE_LABELS must be empty; got {overlap}")

    def test_E3_model_readiness_and_ceiling_capable_disjoint(self):
        overlap = ModelReadiness.MEMBERS & CEILING_CAPABLE_LABELS
        self.assertEqual(overlap, frozenset(),
                         f"ModelReadiness ∩ CEILING_CAPABLE_LABELS must be empty; got {overlap}")

    def test_E4_all_three_pairwise_disjoint(self):
        sets = [OperationalState.MEMBERS, ModelReadiness.MEMBERS, CEILING_CAPABLE_LABELS]
        names = ["OperationalState", "ModelReadiness", "CEILING_CAPABLE_LABELS"]
        for i in range(len(sets)):
            for j in range(i + 1, len(sets)):
                overlap = sets[i] & sets[j]
                self.assertEqual(
                    overlap, frozenset(),
                    f"{names[i]} ∩ {names[j]} must be empty; got {overlap}",
                )


# ─────────────────────────────────────────────────────────────────────────────
# Section F — Isolation: ceiling resolvers do not reference shadow registry
# ─────────────────────────────────────────────────────────────────────────────

class TestCeilingResolverIsolation(unittest.TestCase):

    def _read(self, rel: str) -> str:
        with open(os.path.join(_REPO, rel), encoding="utf-8") as fh:
            return fh.read()

    _SHADOW_SYMBOLS = (
        "kalshi_wx_shadow_registry",
        "OperationalState",
        "ModelReadiness",
        "TerminalProjection",
        "CEILING_CAPABLE_LABELS",
        "is_ceiling_capable",
    )
    _SHARED_MODULE = "kalshi_wx_terminal_labels"

    def _assert_absent(self, src: str, symbol: str, filename: str) -> None:
        self.assertNotIn(
            symbol, src,
            f"{symbol!r} must not appear in {filename}",
        )

    def test_F1_wow_runtime_manifest_isolated(self):
        src = self._read("gate_engine/wow_runtime_manifest.py")
        for sym in self._SHADOW_SYMBOLS + (self._SHARED_MODULE,):
            self._assert_absent(src, sym, "gate_engine/wow_runtime_manifest.py")

    def test_F2_cc_labels_isolated(self):
        src = self._read("gate_engine/command_center/cc_labels.py")
        for sym in self._SHADOW_SYMBOLS + (self._SHARED_MODULE,):
            self._assert_absent(src, sym, "gate_engine/command_center/cc_labels.py")

    def test_F3_ceiling_resolver_isolated(self):
        src = self._read("gate_engine/command_center/ceiling_resolver.py")
        for sym in self._SHADOW_SYMBOLS + (self._SHARED_MODULE,):
            self._assert_absent(src, sym, "gate_engine/command_center/ceiling_resolver.py")


if __name__ == "__main__":
    unittest.main(verbosity=2)
