"""
gate_engine/tests/test_prob_ledger_enforcer.py
WOW-PATCH-2026-08-10-STAGE-A-PROBABILITY-LEDGER-OUTLIER-RECOMPUTE

Tests for gate_engine/prob_ledger_enforcer.py

Coverage
--------
  TestRegistryTaxonomy          — registry is taxonomy-driven (not hardcoded 4 strings);
                                  mandatory fixtures present; synthetic 5th label test.
  TestIsProbabilityBearingLabel — is_probability_bearing_label() edge cases.
  TestEnforceValidLedger        — complete/valid ledger returns ENFORCER_PASS.
  TestMissingFields             — each of the 10 required fields missing individually.
  TestInvalidNumericFields      — NaN, Inf, bool, out-of-range probability values.
  TestBoundViolations           — lower > upper, calibrated outside [lower, upper].
  TestMalformedComponents       — non-list, None, list of non-dicts, mixed.
  TestSourceViolations          — prohibited derivation sources.
  TestNonDictLedger             — non-dict inputs (None, int, str, list, object).
  TestEnforceForLabel           — label-specific dispatch and skip logic.
  TestGovernanceInvariants      — can_execute=False, TERMINAL_LABEL_AUTHORITY=False, etc.
"""
from __future__ import annotations

import math
import unittest
import gate_engine.prob_ledger_enforcer as ple

from gate_engine.prob_ledger_enforcer import (
    PROBABILITY_BEARING_LABELS,
    MANDATORY_REGRESSION_FIXTURES,
    ALL_REQUIRED_LEDGER_FIELDS,
    EnforcementResult,
    enforce,
    enforce_for_label,
    is_probability_bearing_label,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_complete_ledger(**overrides) -> dict:
    """
    Return a ledger dict that passes all validation checks.
    All 7 Stage-2 fields set; components list with all 3 required names.
    """
    base = {
        "raw_probability":          0.62,
        "calibrated_probability":   0.60,
        "lower_bound":              0.52,
        "upper_bound":              0.68,
        "model_timestamp":          "2026-08-10T10:00:00Z",
        "source_snapshot_id":       "snap_abc123",
        "calibration_method":       "ISOTONIC_REGRESSION",
        "components": [
            {"name": "market_no_vig",    "weight": 0.45, "value": 0.61},
            {"name": "l10_distribution", "weight": 0.30, "value": 0.58},
            {"name": "role_usage",       "weight": 0.15, "value": 0.65},
        ],
    }
    base.update(overrides)
    return base


# ---------------------------------------------------------------------------
# TestRegistryTaxonomy
# ---------------------------------------------------------------------------

class TestRegistryTaxonomy(unittest.TestCase):
    """
    Registry is taxonomy-driven (two layers, built at import time).
    Enforcement is never hardcoded to exactly 4 label names.
    """

    def test_mandatory_fixtures_are_in_registry(self):
        """All 4 named mandatory regression fixtures must be members."""
        for label in MANDATORY_REGRESSION_FIXTURES:
            with self.subTest(label=label):
                self.assertIn(label, PROBABILITY_BEARING_LABELS)

    def test_registry_has_more_than_four_members(self):
        """
        Registry must contain more than the 4 mandatory fixtures —
        proving Layer 2 (extended labels) is also included.
        """
        self.assertGreater(len(PROBABILITY_BEARING_LABELS), 4)

    def test_extended_labels_present(self):
        """Layer-2 labels identified in the taxonomy audit must be in the registry."""
        for label in ("MARKET_VERIFIED_HOLD_STALE", "FINAL_CONFIDENCE_HIGH",
                      "FINAL_LOCK", "EDGE_QUALIFIED"):
            with self.subTest(label=label):
                self.assertIn(label, PROBABILITY_BEARING_LABELS)

    def test_non_qualifying_labels_not_in_registry(self):
        """Reject/non-qualifying labels must NOT be in the probability-bearing registry."""
        for label in ("REJECT_NO_EDGE", "SLATE_PURGE", "RESEARCH_INTEREST",
                      "SOURCE_CONFLICT", "NO_PLAY", "DATA_CONTRACT_FAIL"):
            with self.subTest(label=label):
                self.assertNotIn(label, PROBABILITY_BEARING_LABELS)

    def test_registry_is_frozenset(self):
        self.assertIsInstance(PROBABILITY_BEARING_LABELS, frozenset)

    def test_registry_members_are_strings(self):
        for lbl in PROBABILITY_BEARING_LABELS:
            with self.subTest(lbl=lbl):
                self.assertIsInstance(lbl, str)

    def test_synthetic_5th_label_is_governed_by_registry(self):
        """
        Proves enforcement is NOT hardcoded to exactly 4 specific label names.

        A synthetic label (never mentioned anywhere else in this patch) is
        temporarily added to _PROB_BEARING_EXTENDED and PROBABILITY_BEARING_LABELS.
        enforce_for_label() must govern it identically to the named fixtures:
          - complete ledger  → ENFORCER_PASS,  label_is_probability_bearing=True
          - incomplete ledger → ENFORCER_FAIL_*, label_is_probability_bearing=True
        """
        synthetic = "SYNTHETIC_QUALIFYING_TEST_LABEL_ALPHA_001"
        self.assertNotIn(synthetic, PROBABILITY_BEARING_LABELS,
                         "synthetic label must not already be in the registry")

        # Temporarily patch module-level frozensets
        orig_extended  = ple._PROB_BEARING_EXTENDED
        orig_registry  = ple.PROBABILITY_BEARING_LABELS

        ple._PROB_BEARING_EXTENDED     = orig_extended | {synthetic}
        ple.PROBABILITY_BEARING_LABELS = frozenset(
            {lbl.value for lbl in ple._PROB_BEARING_PROP_LABELS}
            | ple._PROB_BEARING_EXTENDED
        )

        try:
            # Registry now contains the synthetic label
            self.assertIn(synthetic, ple.PROBABILITY_BEARING_LABELS)
            self.assertTrue(is_probability_bearing_label(synthetic))

            # Complete ledger → PASS (same contract as any named fixture)
            ok = enforce_for_label(_make_complete_ledger(), synthetic)
            self.assertTrue(ok.enforcer_passed,
                            f"Complete ledger under synthetic label should PASS; violations={ok.violations}")
            self.assertEqual(ok.enforcement_code, "ENFORCER_PASS")
            self.assertTrue(ok.label_is_probability_bearing)
            self.assertFalse(ok.can_execute)
            self.assertFalse(ok.terminal_label_authority)

            # Incomplete ledger → FAIL (same contract as any named fixture)
            fail = enforce_for_label({}, synthetic)
            self.assertFalse(fail.enforcer_passed)
            self.assertIn("FAIL", fail.enforcement_code)
            self.assertTrue(fail.label_is_probability_bearing)
            self.assertGreater(len(fail.violations), 0)

        finally:
            ple._PROB_BEARING_EXTENDED     = orig_extended
            ple.PROBABILITY_BEARING_LABELS = orig_registry

    def test_sixth_extended_label_added_to_layer2_is_governed(self):
        """
        A label added only to _PROB_BEARING_EXTENDED (not PropLabel enum)
        is automatically included in PROBABILITY_BEARING_LABELS.
        Confirms the two-layer union pattern works for future labels.
        """
        new_label = "FUTURE_QUALIFYING_LABEL_BETA_999"
        self.assertNotIn(new_label, ple.PROBABILITY_BEARING_LABELS)

        orig_ext  = ple._PROB_BEARING_EXTENDED
        orig_reg  = ple.PROBABILITY_BEARING_LABELS

        ple._PROB_BEARING_EXTENDED     = orig_ext | {new_label}
        ple.PROBABILITY_BEARING_LABELS = frozenset(
            {lbl.value for lbl in ple._PROB_BEARING_PROP_LABELS}
            | ple._PROB_BEARING_EXTENDED
        )

        try:
            self.assertIn(new_label, ple.PROBABILITY_BEARING_LABELS)
            self.assertTrue(is_probability_bearing_label(new_label))
        finally:
            ple._PROB_BEARING_EXTENDED     = orig_ext
            ple.PROBABILITY_BEARING_LABELS = orig_reg

    def test_all_required_ledger_fields_constant(self):
        """ALL_REQUIRED_LEDGER_FIELDS must have exactly 10 entries (7 + 3)."""
        self.assertEqual(len(ALL_REQUIRED_LEDGER_FIELDS), 10)

    def test_all_required_ledger_fields_contains_stage2_fields(self):
        for f in ("raw_probability", "calibrated_probability", "lower_bound",
                  "upper_bound", "model_timestamp", "source_snapshot_id",
                  "calibration_method"):
            self.assertIn(f, ALL_REQUIRED_LEDGER_FIELDS)

    def test_all_required_ledger_fields_contains_component_names(self):
        for c in ("component:market_no_vig", "component:l10_distribution",
                  "component:role_usage"):
            self.assertIn(c, ALL_REQUIRED_LEDGER_FIELDS)


# ---------------------------------------------------------------------------
# TestIsProbabilityBearingLabel
# ---------------------------------------------------------------------------

class TestIsProbabilityBearingLabel(unittest.TestCase):

    def test_known_qualifying_labels_return_true(self):
        for label in PROBABILITY_BEARING_LABELS:
            with self.subTest(label=label):
                self.assertTrue(is_probability_bearing_label(label))

    def test_reject_labels_return_false(self):
        for label in ("REJECT_NO_EDGE", "SLATE_PURGE", "NO_PLAY",
                      "REJECT_DATA_QUALITY", "SOURCE_CONFLICT"):
            with self.subTest(label=label):
                self.assertFalse(is_probability_bearing_label(label))

    def test_none_returns_false(self):
        self.assertFalse(is_probability_bearing_label(None))

    def test_int_returns_false(self):
        self.assertFalse(is_probability_bearing_label(42))

    def test_empty_string_returns_false(self):
        self.assertFalse(is_probability_bearing_label(""))

    def test_blank_string_returns_false(self):
        self.assertFalse(is_probability_bearing_label("   "))

    def test_bool_returns_false(self):
        self.assertFalse(is_probability_bearing_label(True))

    def test_list_returns_false(self):
        self.assertFalse(is_probability_bearing_label(["MODEL_QUALIFIED_HOLD"]))


# ---------------------------------------------------------------------------
# TestEnforceValidLedger
# ---------------------------------------------------------------------------

class TestEnforceValidLedger(unittest.TestCase):

    def test_complete_ledger_passes(self):
        result = enforce(_make_complete_ledger())
        self.assertTrue(result.enforcer_passed)
        self.assertEqual(result.enforcement_code, "ENFORCER_PASS")
        self.assertEqual(len(result.violations), 0)

    def test_result_is_frozen(self):
        result = enforce(_make_complete_ledger())
        with self.assertRaises((AttributeError, TypeError)):
            result.enforcer_passed = True   # type: ignore

    def test_governance_constants_on_pass_result(self):
        result = enforce(_make_complete_ledger())
        self.assertFalse(result.can_execute)
        self.assertFalse(result.terminal_label_authority)

    def test_boundary_probabilities_pass(self):
        """Probabilities at the open-interval boundaries should be valid."""
        ledger = _make_complete_ledger(
            raw_probability=0.01,
            calibrated_probability=0.01,
            lower_bound=0.01,
            upper_bound=0.99,
        )
        result = enforce(ledger)
        self.assertTrue(result.enforcer_passed, result.violations)

    def test_row_fallback_fills_missing_stage2_fields(self):
        """
        Fields absent from ledger_payload but present in the row dict
        should be used as fallback (mirrors _validate_stage2_schema).
        """
        ledger = _make_complete_ledger()
        model_ts = ledger.pop("model_timestamp")
        result = enforce(ledger, row={"model_timestamp": model_ts})
        self.assertTrue(result.enforcer_passed, result.violations)


# ---------------------------------------------------------------------------
# TestMissingFields
# ---------------------------------------------------------------------------

class TestMissingFields(unittest.TestCase):
    """Each of the 10 required fields missing individually → FAIL."""

    def _assert_field_missing(self, field_key: str, component_name: str | None = None):
        if component_name is None:
            # Stage-2 field
            ledger = _make_complete_ledger()
            del ledger[field_key]
            result = enforce(ledger)
            self.assertFalse(result.enforcer_passed,
                             f"Missing {field_key!r} should fail")
            self.assertIn("FAIL", result.enforcement_code)
            has_missing = any(field_key in m for m in result.missing_fields)
            has_violation = any(field_key in v for v in result.violations)
            self.assertTrue(has_missing or has_violation,
                            f"Expected {field_key!r} in missing_fields or violations; got {result.missing_fields}, {result.violations}")
        else:
            # Component field
            ledger = _make_complete_ledger()
            ledger["components"] = [
                c for c in ledger["components"]
                if c["name"] != component_name
            ]
            result = enforce(ledger)
            self.assertFalse(result.enforcer_passed,
                             f"Missing component {component_name!r} should fail")
            self.assertIn("FAIL", result.enforcement_code)

    def test_missing_raw_probability(self):      self._assert_field_missing("raw_probability")
    def test_missing_calibrated_probability(self): self._assert_field_missing("calibrated_probability")
    def test_missing_lower_bound(self):          self._assert_field_missing("lower_bound")
    def test_missing_upper_bound(self):          self._assert_field_missing("upper_bound")
    def test_missing_model_timestamp(self):      self._assert_field_missing("model_timestamp")
    def test_missing_source_snapshot_id(self):   self._assert_field_missing("source_snapshot_id")
    def test_missing_calibration_method(self):   self._assert_field_missing("calibration_method")
    def test_missing_component_market_no_vig(self):    self._assert_field_missing("_", "market_no_vig")
    def test_missing_component_l10_distribution(self): self._assert_field_missing("_", "l10_distribution")
    def test_missing_component_role_usage(self):       self._assert_field_missing("_", "role_usage")

    def test_empty_ledger_fails_all_fields(self):
        result = enforce({})
        self.assertFalse(result.enforcer_passed)
        self.assertGreater(len(result.violations), 0)

    def test_null_source_snapshot_id_fails(self):
        ledger = _make_complete_ledger(source_snapshot_id=None)
        result = enforce(ledger)
        self.assertFalse(result.enforcer_passed)


# ---------------------------------------------------------------------------
# TestInvalidNumericFields
# ---------------------------------------------------------------------------

class TestInvalidNumericFields(unittest.TestCase):

    def test_nan_calibrated_probability_fails(self):
        ledger = _make_complete_ledger(calibrated_probability=float("nan"))
        result = enforce(ledger)
        self.assertFalse(result.enforcer_passed)

    def test_inf_raw_probability_fails(self):
        ledger = _make_complete_ledger(raw_probability=float("inf"))
        result = enforce(ledger)
        self.assertFalse(result.enforcer_passed)

    def test_negative_inf_lower_bound_fails(self):
        ledger = _make_complete_ledger(lower_bound=float("-inf"))
        result = enforce(ledger)
        self.assertFalse(result.enforcer_passed)

    def test_probability_above_1_fails(self):
        ledger = _make_complete_ledger(
            raw_probability=1.01,
            calibrated_probability=1.01,
            upper_bound=1.01,
        )
        result = enforce(ledger)
        self.assertFalse(result.enforcer_passed)

    def test_probability_zero_fails(self):
        ledger = _make_complete_ledger(
            raw_probability=0.0,
            calibrated_probability=0.0,
            lower_bound=0.0,
        )
        result = enforce(ledger)
        self.assertFalse(result.enforcer_passed)

    def test_probability_exactly_one_fails(self):
        ledger = _make_complete_ledger(
            raw_probability=1.0,
            calibrated_probability=1.0,
            upper_bound=1.0,
        )
        result = enforce(ledger)
        self.assertFalse(result.enforcer_passed)

    def test_bool_true_as_probability_fails(self):
        """True == 1.0 but bool is semantically invalid as a probability."""
        ledger = _make_complete_ledger(calibrated_probability=True)
        result = enforce(ledger)
        self.assertFalse(result.enforcer_passed)

    def test_bool_false_as_probability_fails(self):
        ledger = _make_complete_ledger(calibrated_probability=False)
        result = enforce(ledger)
        self.assertFalse(result.enforcer_passed)


# ---------------------------------------------------------------------------
# TestBoundViolations
# ---------------------------------------------------------------------------

class TestBoundViolations(unittest.TestCase):

    def test_lower_bound_greater_than_upper_bound_fails(self):
        ledger = _make_complete_ledger(lower_bound=0.70, upper_bound=0.60)
        result = enforce(ledger)
        self.assertFalse(result.enforcer_passed)

    def test_calibrated_above_upper_bound_fails(self):
        ledger = _make_complete_ledger(
            lower_bound=0.50, upper_bound=0.65, calibrated_probability=0.70
        )
        result = enforce(ledger)
        self.assertFalse(result.enforcer_passed)

    def test_calibrated_below_lower_bound_fails(self):
        ledger = _make_complete_ledger(
            lower_bound=0.55, upper_bound=0.70, calibrated_probability=0.40
        )
        result = enforce(ledger)
        self.assertFalse(result.enforcer_passed)

    def test_calibrated_equal_to_lower_bound_passes(self):
        ledger = _make_complete_ledger(
            lower_bound=0.55, upper_bound=0.70, calibrated_probability=0.55
        )
        result = enforce(ledger)
        self.assertTrue(result.enforcer_passed, result.violations)

    def test_calibrated_equal_to_upper_bound_passes(self):
        ledger = _make_complete_ledger(
            lower_bound=0.55, upper_bound=0.70, calibrated_probability=0.70
        )
        result = enforce(ledger)
        self.assertTrue(result.enforcer_passed, result.violations)


# ---------------------------------------------------------------------------
# TestMalformedComponents
# ---------------------------------------------------------------------------

class TestMalformedComponents(unittest.TestCase):

    def test_components_none_fails(self):
        ledger = _make_complete_ledger(components=None)
        result = enforce(ledger)
        self.assertFalse(result.enforcer_passed)

    def test_components_not_a_list_fails(self):
        ledger = _make_complete_ledger(components={"market_no_vig": 0.45})
        result = enforce(ledger)
        self.assertFalse(result.enforcer_passed)

    def test_components_list_of_none_fails(self):
        ledger = _make_complete_ledger(components=[None, None, None])
        result = enforce(ledger)
        self.assertFalse(result.enforcer_passed)

    def test_components_list_of_strings_fails(self):
        ledger = _make_complete_ledger(
            components=["market_no_vig", "l10_distribution", "role_usage"]
        )
        result = enforce(ledger)
        self.assertFalse(result.enforcer_passed)

    def test_components_mixed_dict_and_none_fails(self):
        """Mixed list: valid dict + None entries — malformed count reported."""
        ledger = _make_complete_ledger(components=[
            {"name": "market_no_vig", "weight": 0.45, "value": 0.61},
            None,
            {"name": "l10_distribution", "weight": 0.30, "value": 0.58},
        ])
        result = enforce(ledger)
        # role_usage is missing AND there's a malformed entry → FAIL
        self.assertFalse(result.enforcer_passed)

    def test_components_empty_list_fails(self):
        ledger = _make_complete_ledger(components=[])
        result = enforce(ledger)
        self.assertFalse(result.enforcer_passed)

    def test_components_integer_as_list_fails(self):
        ledger = _make_complete_ledger(components=42)
        result = enforce(ledger)
        self.assertFalse(result.enforcer_passed)


# ---------------------------------------------------------------------------
# TestSourceViolations
# ---------------------------------------------------------------------------

class TestSourceViolations(unittest.TestCase):

    def _test_prohibited_source(self, derivation: str):
        ledger = _make_complete_ledger(raw_probability_derivation=derivation)
        result = enforce(ledger)
        self.assertFalse(result.enforcer_passed,
                         f"Derivation {derivation!r} should be rejected")
        self.assertEqual(result.enforcement_code, "ENFORCER_FAIL_MANUFACTURED_PROBABILITY")
        self.assertGreater(len(result.source_violations), 0)

    def test_l5_avg_derivation_fails(self):        self._test_prohibited_source("L5_AVG")
    def test_l10_avg_derivation_fails(self):       self._test_prohibited_source("L10_AVG")
    def test_market_no_vig_derivation_fails(self): self._test_prohibited_source("MARKET_NO_VIG")
    def test_rolling_average_derivation_fails(self): self._test_prohibited_source("ROLLING_AVERAGE")
    def test_naive_hit_rate_derivation_fails(self):  self._test_prohibited_source("NAIVE_HIT_RATE")

    def test_lowercase_derivation_fails(self):
        """Case-insensitive: 'l5_avg' (lower) should also be rejected."""
        ledger = _make_complete_ledger(raw_probability_derivation="l5_avg")
        result = enforce(ledger)
        self.assertFalse(result.enforcer_passed)
        self.assertEqual(result.enforcement_code, "ENFORCER_FAIL_MANUFACTURED_PROBABILITY")

    def test_legitimate_derivation_source_passes(self):
        ledger = _make_complete_ledger(raw_probability_derivation="BINOMIAL_MODEL_V3")
        result = enforce(ledger)
        self.assertTrue(result.enforcer_passed, result.violations)

    def test_no_derivation_field_passes(self):
        ledger = _make_complete_ledger()
        ledger.pop("raw_probability_derivation", None)
        result = enforce(ledger)
        self.assertTrue(result.enforcer_passed, result.violations)


# ---------------------------------------------------------------------------
# TestNonDictLedger
# ---------------------------------------------------------------------------

class TestNonDictLedger(unittest.TestCase):
    """Non-dict inputs are treated as empty ledgers and fail gracefully."""

    def test_none_ledger_fails(self):
        result = enforce(None)
        self.assertFalse(result.enforcer_passed)
        self.assertIsInstance(result, EnforcementResult)

    def test_integer_ledger_fails(self):
        result = enforce(42)
        self.assertFalse(result.enforcer_passed)

    def test_string_ledger_fails(self):
        result = enforce("MODEL_QUALIFIED_HOLD")
        self.assertFalse(result.enforcer_passed)

    def test_list_ledger_fails(self):
        result = enforce(["raw_probability", 0.62])
        self.assertFalse(result.enforcer_passed)

    def test_object_ledger_fails(self):
        result = enforce(object())
        self.assertFalse(result.enforcer_passed)


# ---------------------------------------------------------------------------
# TestEnforceForLabel
# ---------------------------------------------------------------------------

class TestEnforceForLabel(unittest.TestCase):

    # ── Mandatory regression fixtures — each must be governed individually ──

    def _assert_fixture_governs(self, label: str):
        """Complete ledger → PASS; empty ledger → FAIL; label_is_prob_bearing=True."""
        ok = enforce_for_label(_make_complete_ledger(), label)
        self.assertTrue(ok.enforcer_passed, f"{label}: {ok.violations}")
        self.assertEqual(ok.enforcement_code, "ENFORCER_PASS")
        self.assertTrue(ok.label_is_probability_bearing)
        self.assertFalse(ok.can_execute)
        self.assertFalse(ok.terminal_label_authority)

        fail = enforce_for_label({}, label)
        self.assertFalse(fail.enforcer_passed)
        self.assertIn("FAIL", fail.enforcement_code)
        self.assertTrue(fail.label_is_probability_bearing)
        self.assertGreater(len(fail.violations), 0)

    def test_fixture_model_qualified_hold(self):
        self._assert_fixture_governs("MODEL_QUALIFIED_HOLD")

    def test_fixture_market_verified_hold(self):
        self._assert_fixture_governs("MARKET_VERIFIED_HOLD")

    def test_fixture_money_qualified(self):
        self._assert_fixture_governs("MONEY_QUALIFIED")

    def test_fixture_final_confidence_high(self):
        self._assert_fixture_governs("FINAL_CONFIDENCE_HIGH")

    # ── Extended labels (Layer 2) are also governed ─────────────────────

    def test_market_verified_hold_stale_governed(self):
        self._assert_fixture_governs("MARKET_VERIFIED_HOLD_STALE")

    def test_final_lock_governed(self):
        self._assert_fixture_governs("FINAL_LOCK")

    def test_edge_qualified_governed(self):
        self._assert_fixture_governs("EDGE_QUALIFIED")

    # ── Non-probability-bearing labels → SKIP ────────────────────────────

    def _assert_label_skipped(self, label: str):
        result = enforce_for_label(_make_complete_ledger(), label)
        self.assertTrue(result.enforcer_passed)
        self.assertEqual(result.enforcement_code, "ENFORCER_SKIP_NON_PROBABILITY_LABEL")
        self.assertFalse(result.label_is_probability_bearing)
        self.assertEqual(len(result.violations), 0)

    def test_skip_reject_no_edge(self):         self._assert_label_skipped("REJECT_NO_EDGE")
    def test_skip_slate_purge(self):            self._assert_label_skipped("SLATE_PURGE")
    def test_skip_research_interest(self):      self._assert_label_skipped("RESEARCH_INTEREST")
    def test_skip_no_play(self):                self._assert_label_skipped("NO_PLAY")
    def test_skip_data_contract_fail(self):     self._assert_label_skipped("DATA_CONTRACT_FAIL")
    def test_skip_reject_data_quality(self):    self._assert_label_skipped("REJECT_DATA_QUALITY")
    def test_skip_unknown_label(self):          self._assert_label_skipped("TOTALLY_UNKNOWN_LABEL")
    def test_skip_empty_label(self):            self._assert_label_skipped("")

    # ── label_is_probability_bearing flag is correct on FAIL ─────────────

    def test_fail_result_has_label_is_prob_true_for_qualifying_label(self):
        result = enforce_for_label({}, "MONEY_QUALIFIED")
        self.assertFalse(result.enforcer_passed)
        self.assertTrue(result.label_is_probability_bearing)

    # ── All labels in registry are governed (registry-driven proof) ───────

    def test_all_registry_labels_are_governed(self):
        """
        For every label in PROBABILITY_BEARING_LABELS, enforce_for_label with
        an empty ledger must return ENFORCER_FAIL_*, not SKIP.
        Proves the registry drives the dispatch — not a hardcoded list.
        """
        for label in PROBABILITY_BEARING_LABELS:
            with self.subTest(label=label):
                result = enforce_for_label({}, label)
                self.assertFalse(result.enforcer_passed,
                                 f"{label} should fail on empty ledger")
                self.assertIn("FAIL", result.enforcement_code,
                              f"{label}: expected FAIL code, got {result.enforcement_code}")
                self.assertTrue(result.label_is_probability_bearing)


# ---------------------------------------------------------------------------
# TestGovernanceInvariants
# ---------------------------------------------------------------------------

class TestGovernanceInvariants(unittest.TestCase):

    def test_module_can_execute_is_false(self):
        self.assertFalse(ple.can_execute)

    def test_module_production_authority_is_false(self):
        self.assertFalse(ple.PRODUCTION_AUTHORITY)

    def test_module_user_output_authority_is_false(self):
        self.assertFalse(ple.USER_OUTPUT_AUTHORITY)

    def test_module_terminal_label_authority_is_false(self):
        self.assertFalse(ple.TERMINAL_LABEL_AUTHORITY)

    def test_enforcement_result_can_execute_always_false(self):
        for label in ("MODEL_QUALIFIED_HOLD", "REJECT_NO_EDGE", ""):
            with self.subTest(label=label):
                result = enforce_for_label(_make_complete_ledger(), label)
                self.assertFalse(result.can_execute)

    def test_enforcement_result_terminal_label_authority_always_false(self):
        for label in ("MONEY_QUALIFIED", "REJECT_NO_EDGE", ""):
            with self.subTest(label=label):
                result = enforce_for_label(_make_complete_ledger(), label)
                self.assertFalse(result.terminal_label_authority)

    def test_enforcement_result_is_frozen(self):
        result = enforce(_make_complete_ledger())
        # Direct attribute assignment must raise FrozenInstanceError (subclass of
        # AttributeError) because EnforcementResult is @dataclass(frozen=True).
        # object.__setattr__ is intentionally NOT used here — that call bypasses
        # the frozen guard and is the correct way to SET fields in test fixtures,
        # NOT the correct way to verify the guard exists.
        with self.assertRaises((AttributeError, TypeError)):
            result.enforcement_code = "MUTATED_VALUE"  # type: ignore

    def test_no_import_from_app(self):
        import ast, pathlib
        src = pathlib.Path(ple.__file__).read_text()
        tree = ast.parse(src)
        for node in ast.walk(tree):
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                if isinstance(node, ast.ImportFrom) and node.module:
                    self.assertNotIn("app", node.module.split("."),
                                     f"Forbidden import from app in {node.module}")

    def test_no_import_from_universal_agent(self):
        import ast, pathlib
        src = pathlib.Path(ple.__file__).read_text()
        tree = ast.parse(src)
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module:
                self.assertNotIn("universal_agent", node.module,
                                 f"Forbidden import: {node.module}")

    def test_no_import_from_pipeline_state(self):
        import ast, pathlib
        src = pathlib.Path(ple.__file__).read_text()
        tree = ast.parse(src)
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module:
                self.assertNotIn("pipeline_state", node.module,
                                 f"Forbidden import: {node.module}")

    def test_no_import_from_settlement_worker(self):
        import ast, pathlib
        src = pathlib.Path(ple.__file__).read_text()
        tree = ast.parse(src)
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module:
                self.assertNotIn("settlement_worker", node.module,
                                 f"Forbidden import: {node.module}")


if __name__ == "__main__":
    unittest.main()
