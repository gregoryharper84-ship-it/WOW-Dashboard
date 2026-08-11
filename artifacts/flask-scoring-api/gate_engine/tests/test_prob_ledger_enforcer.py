"""
tests/test_prob_ledger_enforcer.py

Unit tests for gate_engine/prob_ledger_enforcer.py — Stage A offline enforcer.

Coverage:
  1. Each of the 10 required fields missing individually → FAIL
  2. Complete ledger → PASS
  3. Bounds and invariant violations → FAIL (non-finite, out-of-range, bool,
     lower>upper, calibrated outside interval)
  4. Probability-bearing label class:
       - All four mandatory regression fixtures apply enforcement
       - Non-qualifying labels skip enforcement
       - FINAL_APPROVED and MARKET_VERIFIED_HOLD_STALE also apply
  5. Later market failure does not erase a previously-obtained PASS result
  6. WNBA points/assists/rebounds fixtures (complete → PASS, partial → FAIL)
  7. MLB pitcher strikeouts fixtures (complete → PASS, partial → FAIL)
  8. Mixed-batch independence (per-row states do not bleed)
  9. Manufactured-probability source violation
 10. Governance invariants: terminal_label_authority=False, can_execute=False

No network calls. No database calls. All synthetic.
"""
import math
import unittest

from gate_engine.prob_ledger_enforcer import (
    ALL_REQUIRED_LEDGER_FIELDS,
    PROBABILITY_BEARING_LABELS,
    EnforcementResult,
    enforce,
    enforce_for_label,
    is_probability_bearing_label,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _complete_ledger() -> dict:
    """
    A fully-populated, valid model_probability_ledger dict containing all
    10 required fields (7 stage-2 + 3 required components).
    """
    return {
        # Stage-2 schema fields (7)
        "raw_probability":        0.58,
        "calibrated_probability": 0.55,
        "lower_bound":            0.47,
        "upper_bound":            0.63,
        "model_timestamp":        "2026-08-11T12:00:00Z",
        "source_snapshot_id":     "snap_abc123",
        "calibration_method":     "platt",
        # Required components (3)
        "components": [
            {"name": "market_no_vig",    "weight": 0.45, "value": 0.57, "source": "odds_api"},
            {"name": "l10_distribution", "weight": 0.30, "value": 0.54, "source": "game_log"},
            {"name": "role_usage",       "weight": 0.15, "value": 0.60, "source": "minutes_model"},
        ],
    }


def _complete_row(sport: str = "WNBA", prop_type: str = "PTS") -> dict:
    """Minimal row dict for pass-through fallback testing."""
    return {
        "sport":     sport,
        "prop_type": prop_type,
        "player":    "Test Player",
        "row_id":    "test_row_001",
        "blockers":  [],
        "gates":     {},
    }


# ---------------------------------------------------------------------------
# Helper: assert all governance invariants hold on any EnforcementResult
# ---------------------------------------------------------------------------

def _assert_governance(tc: unittest.TestCase, result: EnforcementResult) -> None:
    tc.assertFalse(result.terminal_label_authority,
                   "terminal_label_authority must always be False")
    tc.assertFalse(result.can_execute,
                   "can_execute must always be False")
    tc.assertIsInstance(result.violations, tuple)
    tc.assertIsInstance(result.missing_fields, tuple)
    tc.assertIsInstance(result.invalid_fields, tuple)
    tc.assertIsInstance(result.source_violations, tuple)


# ===========================================================================
# 1. Each missing field → FAIL
# ===========================================================================

class TestEachMissingFieldFails(unittest.TestCase):
    """Removing any single required field from a complete ledger must fail."""

    STAGE2_FIELDS = (
        "raw_probability",
        "calibrated_probability",
        "lower_bound",
        "upper_bound",
        "model_timestamp",
        "source_snapshot_id",
        "calibration_method",
    )
    COMPONENT_NAMES = ("market_no_vig", "l10_distribution", "role_usage")

    def _test_field_missing(self, field_name: str) -> None:
        ledger = _complete_ledger()
        ledger.pop(field_name, None)
        result = enforce(ledger)
        _assert_governance(self, result)
        self.assertFalse(result.enforcer_passed,
                         f"enforcer must FAIL when {field_name!r} is missing")
        # Violation string must reference the missing field
        combined = " ".join(result.violations)
        self.assertIn(field_name, combined,
                      f"violations must mention {field_name!r}")

    def _test_component_missing(self, component_name: str) -> None:
        ledger = _complete_ledger()
        ledger["components"] = [
            c for c in ledger["components"]
            if c["name"] != component_name
        ]
        result = enforce(ledger)
        _assert_governance(self, result)
        self.assertFalse(result.enforcer_passed,
                         f"enforcer must FAIL when component {component_name!r} is absent")
        combined = " ".join(result.missing_fields)
        self.assertIn(component_name, combined,
                      f"missing_fields must mention {component_name!r}")

    def test_missing_raw_probability(self):
        self._test_field_missing("raw_probability")

    def test_missing_calibrated_probability(self):
        self._test_field_missing("calibrated_probability")

    def test_missing_lower_bound(self):
        self._test_field_missing("lower_bound")

    def test_missing_upper_bound(self):
        self._test_field_missing("upper_bound")

    def test_missing_model_timestamp(self):
        self._test_field_missing("model_timestamp")

    def test_missing_source_snapshot_id(self):
        self._test_field_missing("source_snapshot_id")

    def test_missing_calibration_method(self):
        self._test_field_missing("calibration_method")

    def test_missing_component_market_no_vig(self):
        self._test_component_missing("market_no_vig")

    def test_missing_component_l10_distribution(self):
        self._test_component_missing("l10_distribution")

    def test_missing_component_role_usage(self):
        self._test_component_missing("role_usage")

    def test_none_raw_probability_fails(self):
        ledger = _complete_ledger()
        ledger["raw_probability"] = None
        result = enforce(ledger)
        self.assertFalse(result.enforcer_passed)
        _assert_governance(self, result)

    def test_empty_string_model_timestamp_fails(self):
        ledger = _complete_ledger()
        ledger["model_timestamp"] = "   "
        result = enforce(ledger)
        self.assertFalse(result.enforcer_passed)
        _assert_governance(self, result)

    def test_empty_components_list_fails_all_three(self):
        ledger = _complete_ledger()
        ledger["components"] = []
        result = enforce(ledger)
        self.assertFalse(result.enforcer_passed)
        # All three required component names must appear in missing_fields
        combined = " ".join(result.missing_fields)
        for comp in ("market_no_vig", "l10_distribution", "role_usage"):
            self.assertIn(comp, combined)

    def test_all_10_fields_present_confirm_count(self):
        """Sanity: ALL_REQUIRED_LEDGER_FIELDS should have exactly 10 entries."""
        self.assertEqual(len(ALL_REQUIRED_LEDGER_FIELDS), 10)


# ===========================================================================
# 2. Complete ledger → PASS
# ===========================================================================

class TestCompleteLedgerPasses(unittest.TestCase):

    def test_complete_ledger_passes(self):
        result = enforce(_complete_ledger())
        _assert_governance(self, result)
        self.assertTrue(result.enforcer_passed)
        self.assertEqual(result.enforcement_code, "ENFORCER_PASS")
        self.assertEqual(len(result.violations), 0)
        self.assertEqual(len(result.missing_fields), 0)

    def test_complete_ledger_with_optional_components_still_passes(self):
        ledger = _complete_ledger()
        ledger["components"].append(
            {"name": "l5_trend", "weight": 0.04, "value": 0.03, "source": "trend_model"}
        )
        result = enforce(ledger)
        self.assertTrue(result.enforcer_passed)

    def test_complete_ledger_with_row_fallback(self):
        """Stage-2 fields can fall through from the row dict."""
        ledger = _complete_ledger()
        # Move model_timestamp to the row instead of the ledger
        row = _complete_row()
        row["model_timestamp"] = ledger.pop("model_timestamp")
        result = enforce(ledger, row=row)
        # _validate_stage2_schema checks row as fallback — should still pass
        self.assertTrue(result.enforcer_passed)


# ===========================================================================
# 3. Bounds and invariant violations
# ===========================================================================

class TestBoundsAndInvariantViolations(unittest.TestCase):

    def _fail_with(self, field: str, bad_value) -> EnforcementResult:
        ledger = _complete_ledger()
        ledger[field] = bad_value
        result = enforce(ledger)
        self.assertFalse(result.enforcer_passed,
                         f"enforcer must FAIL for {field}={bad_value!r}")
        _assert_governance(self, result)
        return result

    def test_raw_probability_nan_fails(self):
        self._fail_with("raw_probability", float("nan"))

    def test_raw_probability_inf_fails(self):
        self._fail_with("raw_probability", float("inf"))

    def test_raw_probability_zero_fails(self):
        # Open interval (0, 1): exactly 0.0 must fail
        self._fail_with("raw_probability", 0.0)

    def test_raw_probability_one_fails(self):
        # Open interval (0, 1): exactly 1.0 must fail
        self._fail_with("raw_probability", 1.0)

    def test_raw_probability_negative_fails(self):
        self._fail_with("raw_probability", -0.1)

    def test_raw_probability_above_one_fails(self):
        self._fail_with("raw_probability", 1.05)

    def test_calibrated_probability_nan_fails(self):
        self._fail_with("calibrated_probability", float("nan"))

    def test_lower_bound_greater_than_upper_bound_fails(self):
        ledger = _complete_ledger()
        ledger["lower_bound"] = 0.70
        ledger["upper_bound"] = 0.50
        result = enforce(ledger)
        self.assertFalse(result.enforcer_passed)
        combined = " ".join(result.violations + result.invalid_fields)
        self.assertTrue(
            "lower_bound" in combined or "upper_bound" in combined,
            "violation must mention bounds"
        )

    def test_calibrated_outside_interval_fails(self):
        # calibrated_probability must be in [lower_bound, upper_bound]
        ledger = _complete_ledger()
        ledger["lower_bound"]            = 0.40
        ledger["upper_bound"]            = 0.50
        ledger["calibrated_probability"] = 0.60  # outside interval
        result = enforce(ledger)
        self.assertFalse(result.enforcer_passed)

    def test_bool_raw_probability_fails(self):
        # bool is a subclass of int; True==1.0 would pass a naive range check
        self._fail_with("raw_probability", True)

    def test_bool_calibrated_probability_fails(self):
        self._fail_with("calibrated_probability", False)

    def test_string_raw_probability_non_numeric_fails(self):
        self._fail_with("raw_probability", "not-a-number")

    def test_calibrated_below_lower_bound_fails(self):
        ledger = _complete_ledger()
        ledger["lower_bound"]            = 0.50
        ledger["upper_bound"]            = 0.70
        ledger["calibrated_probability"] = 0.30  # below lower_bound
        result = enforce(ledger)
        self.assertFalse(result.enforcer_passed)


# ===========================================================================
# 4. Probability-bearing label class
# ===========================================================================

class TestProbabilityBearingLabelClass(unittest.TestCase):

    # Mandatory regression fixtures
    MANDATORY_FIXTURES = (
        "MODEL_QUALIFIED_HOLD",
        "MARKET_VERIFIED_HOLD",
        "MONEY_QUALIFIED",
        "FINAL_CONFIDENCE_HIGH",
    )

    def test_mandatory_fixtures_in_class(self):
        for label in self.MANDATORY_FIXTURES:
            self.assertIn(label, PROBABILITY_BEARING_LABELS,
                          f"{label} must be in PROBABILITY_BEARING_LABELS")

    def test_mandatory_fixtures_are_probability_bearing(self):
        for label in self.MANDATORY_FIXTURES:
            self.assertTrue(is_probability_bearing_label(label),
                            f"is_probability_bearing_label({label!r}) must be True")

    def test_final_approved_is_probability_bearing(self):
        self.assertTrue(is_probability_bearing_label("FINAL_APPROVED"))

    def test_market_verified_hold_stale_is_probability_bearing(self):
        self.assertTrue(is_probability_bearing_label("MARKET_VERIFIED_HOLD_STALE"))

    def test_reject_labels_are_not_probability_bearing(self):
        non_prob = [
            "REJECT_NO_EDGE",
            "REJECT_DATA_QUALITY",
            "RESEARCH_INTEREST",
            "SLATE_PURGE",
            "REJECT_BAD_STRUCTURE",
            "DUPLICATE_EXPOSURE_BLOCK",
            "SOURCE_CONFLICT",
            "DATA_CONTRACT_FAIL",
        ]
        for label in non_prob:
            self.assertFalse(is_probability_bearing_label(label),
                             f"{label} must NOT be probability-bearing")

    def test_none_and_empty_not_probability_bearing(self):
        self.assertFalse(is_probability_bearing_label(None))
        self.assertFalse(is_probability_bearing_label(""))
        self.assertFalse(is_probability_bearing_label("   "))

    def test_enforce_for_label_skips_non_probability_labels(self):
        # A completely empty ledger should still PASS for a non-qualifying label
        result = enforce_for_label({}, label="RESEARCH_INTEREST")
        _assert_governance(self, result)
        self.assertTrue(result.enforcer_passed)
        self.assertFalse(result.label_is_probability_bearing)
        self.assertEqual(result.enforcement_code, "ENFORCER_SKIP_NON_PROBABILITY_LABEL")

    def test_enforce_for_label_applies_to_model_qualified_hold(self):
        result = enforce_for_label({}, label="MODEL_QUALIFIED_HOLD")
        self.assertFalse(result.enforcer_passed)  # empty ledger should fail
        self.assertTrue(result.label_is_probability_bearing)

    def test_enforce_for_label_complete_ledger_mandatory_fixtures(self):
        for label in self.MANDATORY_FIXTURES:
            result = enforce_for_label(_complete_ledger(), label=label)
            self.assertTrue(result.enforcer_passed,
                            f"complete ledger must PASS for {label}")
            self.assertTrue(result.label_is_probability_bearing)

    def test_label_class_not_just_four_names(self):
        # The class must contain MORE than the four mandatory fixtures
        self.assertGreater(len(PROBABILITY_BEARING_LABELS), 4,
                           "PROBABILITY_BEARING_LABELS must define the full class, "
                           "not just the four mandatory regression fixtures")


# ===========================================================================
# 5. Later market failure preserves PASS result
# ===========================================================================

class TestLedgerPreservationAfterMarketFailure(unittest.TestCase):
    """
    Once the enforcer returns a PASS result, adding downstream failures
    (market contradictions, blockers, label changes) to the row must NOT
    alter the previously-obtained EnforcementResult.
    """

    def test_pass_result_unchanged_after_market_failure_added(self):
        ledger = _complete_ledger()
        row    = _complete_row()

        # Step 1: enforce on a clean row → PASS
        result_before = enforce(ledger, row=row)
        self.assertTrue(result_before.enforcer_passed)

        # Step 2: simulate a downstream market gate failure by mutating the row
        row["blockers"].append("MARKET_CONTRADICTION:LINE_MOVED_AGAINST_MODEL")
        row["gates"]["market_gate"] = {"market_status": "MARKET_CONTRADICTION", "passed": False}
        row["terminal_label"] = "MARKET_VERIFIED_HOLD"

        # Step 3: re-enforce (caller re-runs; this simulates checking preservation)
        result_after = enforce(ledger, row=row)

        # The original result object is frozen — cannot be mutated.
        self.assertTrue(result_before.enforcer_passed,
                        "frozen EnforcementResult must be unchanged after row mutation")
        # Re-enforcing on the same ledger must still PASS (ledger itself unchanged)
        self.assertTrue(result_after.enforcer_passed,
                        "re-enforcement on unchanged ledger must still PASS")

    def test_enforcer_result_is_frozen(self):
        result = enforce(_complete_ledger())
        with self.assertRaises((AttributeError, TypeError)):
            result.enforcer_passed = False  # type: ignore  # frozen dataclass

    def test_ledger_payload_is_not_mutated(self):
        ledger = _complete_ledger()
        import copy
        ledger_before = copy.deepcopy(ledger)
        enforce(ledger)
        self.assertEqual(ledger, ledger_before,
                         "enforce() must not mutate the input ledger dict")

    def test_row_is_not_mutated(self):
        row = _complete_row()
        import copy
        row_before = copy.deepcopy(row)
        enforce(_complete_ledger(), row=row)
        self.assertEqual(row, row_before,
                         "enforce() must not mutate the input row dict")


# ===========================================================================
# 6. WNBA points / assists / rebounds fixtures
# ===========================================================================

class TestWNBAFixtures(unittest.TestCase):

    def _wnba_row(self, prop_type: str) -> dict:
        return _complete_row(sport="WNBA", prop_type=prop_type)

    def test_wnba_points_complete_ledger_passes(self):
        result = enforce_for_label(
            _complete_ledger(), label="MODEL_QUALIFIED_HOLD",
            row=self._wnba_row("PTS"),
        )
        self.assertTrue(result.enforcer_passed)

    def test_wnba_points_missing_source_snapshot_id_fails(self):
        ledger = _complete_ledger()
        ledger.pop("source_snapshot_id")
        result = enforce_for_label(ledger, label="MODEL_QUALIFIED_HOLD",
                                   row=self._wnba_row("PTS"))
        self.assertFalse(result.enforcer_passed)
        self.assertIn("source_snapshot_id", " ".join(result.missing_fields))

    def test_wnba_assists_complete_ledger_passes(self):
        result = enforce_for_label(
            _complete_ledger(), label="MARKET_VERIFIED_HOLD",
            row=self._wnba_row("AST"),
        )
        self.assertTrue(result.enforcer_passed)

    def test_wnba_assists_missing_calibration_method_fails(self):
        ledger = _complete_ledger()
        ledger.pop("calibration_method")
        result = enforce_for_label(ledger, label="MARKET_VERIFIED_HOLD",
                                   row=self._wnba_row("AST"))
        self.assertFalse(result.enforcer_passed)

    def test_wnba_rebounds_complete_ledger_passes(self):
        result = enforce_for_label(
            _complete_ledger(), label="MONEY_QUALIFIED",
            row=self._wnba_row("REB"),
        )
        self.assertTrue(result.enforcer_passed)

    def test_wnba_rebounds_missing_role_usage_component_fails(self):
        ledger = _complete_ledger()
        ledger["components"] = [c for c in ledger["components"]
                                 if c["name"] != "role_usage"]
        result = enforce_for_label(ledger, label="MONEY_QUALIFIED",
                                   row=self._wnba_row("REB"))
        self.assertFalse(result.enforcer_passed)
        self.assertIn("role_usage", " ".join(result.missing_fields))

    def test_wnba_points_raw_probability_nan_fails(self):
        ledger = _complete_ledger()
        ledger["raw_probability"] = float("nan")
        result = enforce_for_label(ledger, label="MODEL_QUALIFIED_HOLD",
                                   row=self._wnba_row("PTS"))
        self.assertFalse(result.enforcer_passed)


# ===========================================================================
# 7. MLB pitcher strikeouts fixtures
# ===========================================================================

class TestMLBStrikeoutsFixtures(unittest.TestCase):

    def _mlb_row(self, prop_type: str = "SO") -> dict:
        return _complete_row(sport="MLB", prop_type=prop_type)

    def test_mlb_strikeouts_complete_ledger_passes(self):
        result = enforce_for_label(
            _complete_ledger(), label="MODEL_QUALIFIED_HOLD",
            row=self._mlb_row("SO"),
        )
        self.assertTrue(result.enforcer_passed)

    def test_mlb_strikeouts_missing_market_no_vig_component_fails(self):
        ledger = _complete_ledger()
        ledger["components"] = [c for c in ledger["components"]
                                 if c["name"] != "market_no_vig"]
        result = enforce_for_label(ledger, label="MODEL_QUALIFIED_HOLD",
                                   row=self._mlb_row("SO"))
        self.assertFalse(result.enforcer_passed)

    def test_mlb_strikeouts_missing_lower_bound_fails(self):
        ledger = _complete_ledger()
        ledger.pop("lower_bound")
        result = enforce_for_label(ledger, label="MARKET_VERIFIED_HOLD",
                                   row=self._mlb_row("K"))
        self.assertFalse(result.enforcer_passed)
        self.assertIn("lower_bound", " ".join(result.missing_fields))

    def test_mlb_strikeouts_invalid_upper_bound_fails(self):
        ledger = _complete_ledger()
        ledger["upper_bound"] = 1.5  # > 1.0: invalid
        result = enforce_for_label(ledger, label="MODEL_QUALIFIED_HOLD",
                                   row=self._mlb_row("STRIKEOUTS"))
        self.assertFalse(result.enforcer_passed)

    def test_mlb_strikeouts_final_confidence_high_label(self):
        """FINAL_CONFIDENCE_HIGH is a mandatory regression fixture label."""
        result = enforce_for_label(
            _complete_ledger(), label="FINAL_CONFIDENCE_HIGH",
            row=self._mlb_row("SO"),
        )
        self.assertTrue(result.enforcer_passed)
        self.assertTrue(result.label_is_probability_bearing)


# ===========================================================================
# 8. Mixed-batch independence
# ===========================================================================

class TestMixedBatchIndependence(unittest.TestCase):
    """
    Per-row states must be independent.  Passing/failing one row must not
    affect another.
    """

    def test_five_row_batch_independent_states(self):
        rows = [
            # (ledger_modification_fn, expected_pass)
            (lambda l: l,                         True),   # complete
            (lambda l: l.__setitem__("raw_probability", None) or l,  False),  # missing
            (lambda l: l,                         True),   # complete again
            (lambda l: l.__setitem__("calibration_method", "") or l, False),  # empty
            (lambda l: l,                         True),   # complete again
        ]
        import copy
        results = []
        for mod_fn, _ in rows:
            ledger = mod_fn(copy.deepcopy(_complete_ledger()))
            results.append(enforce(ledger))

        expected = [True, False, True, False, True]
        for i, (result, exp) in enumerate(zip(results, expected)):
            self.assertEqual(
                result.enforcer_passed, exp,
                f"Row {i}: expected enforcer_passed={exp}, got {result.enforcer_passed}"
            )
            _assert_governance(self, result)

    def test_parallel_enforce_calls_independent(self):
        """
        Calling enforce() multiple times with different ledgers does not
        share state between calls.
        """
        ledger_good = _complete_ledger()
        ledger_bad  = _complete_ledger()
        del ledger_bad["model_timestamp"]

        r1 = enforce(ledger_good)
        r2 = enforce(ledger_bad)
        r3 = enforce(ledger_good)

        self.assertTrue(r1.enforcer_passed)
        self.assertFalse(r2.enforcer_passed)
        self.assertTrue(r3.enforcer_passed)


# ===========================================================================
# 9b. Malformed components containers and members
# ===========================================================================

class TestMalformedComponentsInput(unittest.TestCase):
    """
    enforce() must never raise when components entries are not dicts.
    Every malformed structure must return a structured EnforcementResult(FAIL).
    """

    def _assert_fails_gracefully(self, components_value, label: str = "") -> EnforcementResult:
        ledger = _complete_ledger()
        ledger["components"] = components_value
        result = enforce(ledger)
        _assert_governance(self, result)
        self.assertFalse(result.enforcer_passed, f"should FAIL for {label!r}")
        self.assertIsInstance(result, EnforcementResult)
        return result

    def test_components_is_none_fails(self):
        result = self._assert_fails_gracefully(None, "components=None")
        # All three required components must be reported missing
        combined = " ".join(result.missing_fields)
        for name in ("market_no_vig", "l10_distribution", "role_usage"):
            self.assertIn(name, combined)

    def test_components_is_not_a_list_fails(self):
        """components as a dict (not a list) must fail gracefully."""
        ledger = _complete_ledger()
        ledger["components"] = {"name": "market_no_vig", "weight": 0.45}
        result = enforce(ledger)
        self.assertFalse(result.enforcer_passed)
        _assert_governance(self, result)

    def test_components_is_string_fails(self):
        self._assert_fails_gracefully("market_no_vig", "components=string")

    def test_components_is_integer_fails(self):
        self._assert_fails_gracefully(42, "components=int")

    def test_components_with_none_entry_fails(self):
        """A list containing None instead of a dict must fail without AttributeError."""
        ledger = _complete_ledger()
        ledger["components"] = [None, None, None]
        result = enforce(ledger)
        self.assertFalse(result.enforcer_passed)
        _assert_governance(self, result)

    def test_components_with_string_entries_fails(self):
        """List of strings is not dicts."""
        ledger = _complete_ledger()
        ledger["components"] = ["market_no_vig", "l10_distribution", "role_usage"]
        result = enforce(ledger)
        self.assertFalse(result.enforcer_passed)
        _assert_governance(self, result)
        # Malformed-entry violation must be reported
        combined = " ".join(result.violations + result.invalid_fields)
        self.assertIn("malformed", combined.lower())

    def test_components_mixed_valid_and_none_fails(self):
        """One valid dict + two None entries — still fails due to malformed."""
        ledger = _complete_ledger()
        valid_comp = {"name": "market_no_vig", "weight": 0.45, "value": 0.57, "source": "odds_api"}
        ledger["components"] = [valid_comp, None, None]
        result = enforce(ledger)
        self.assertFalse(result.enforcer_passed)
        _assert_governance(self, result)
        # l10_distribution and role_usage are still missing
        combined = " ".join(result.missing_fields)
        self.assertIn("l10_distribution", combined)
        self.assertIn("role_usage", combined)

    def test_components_mixed_valid_and_string_fails(self):
        ledger = _complete_ledger()
        good = {"name": "market_no_vig", "weight": 0.45, "value": 0.57, "source": "odds_api"}
        ledger["components"] = [good, "l10_distribution", "role_usage"]
        result = enforce(ledger)
        self.assertFalse(result.enforcer_passed)
        _assert_governance(self, result)

    def test_components_all_three_dicts_but_wrong_names_fails(self):
        """Dicts that don't have required names still fail."""
        ledger = _complete_ledger()
        ledger["components"] = [
            {"name": "unknown_a", "weight": 0.33},
            {"name": "unknown_b", "weight": 0.33},
            {"name": "unknown_c", "weight": 0.34},
        ]
        result = enforce(ledger)
        self.assertFalse(result.enforcer_passed)
        combined = " ".join(result.missing_fields)
        for name in ("market_no_vig", "l10_distribution", "role_usage"):
            self.assertIn(name, combined)

    def test_empty_dict_entry_fails_gracefully(self):
        """Dict with no 'name' key is invalid but must not raise."""
        ledger = _complete_ledger()
        ledger["components"] = [{}, {}, {}]
        result = enforce(ledger)
        self.assertFalse(result.enforcer_passed)
        _assert_governance(self, result)

    def test_enforce_never_raises_on_pathological_components(self):
        """
        A comprehensive set of pathological components values must all return
        an EnforcementResult without raising any exception.
        """
        pathological = [
            None,
            42,
            "string",
            3.14,
            {"not": "a_list"},
            [None],
            [None, None, None],
            ["market_no_vig", "l10_distribution", "role_usage"],
            [{"name": None}],
            [{"name": 42}],
            [[{"nested": "list"}]],
            [object()],
        ]
        ledger_base = {k: v for k, v in _complete_ledger().items() if k != "components"}
        for bad_components in pathological:
            ledger = {**ledger_base, "components": bad_components}
            try:
                result = enforce(ledger)
                self.assertIsInstance(result, EnforcementResult,
                                     f"enforce() must return EnforcementResult for "
                                     f"components={bad_components!r}")
                self.assertFalse(result.enforcer_passed,
                                 f"malformed components must fail for {bad_components!r}")
            except Exception as exc:
                self.fail(
                    f"enforce() raised {type(exc).__name__} for "
                    f"components={bad_components!r}: {exc}"
                )

    def test_valid_components_still_pass_after_fix(self):
        """Regression: the normal path must still pass after the guard was added."""
        result = enforce(_complete_ledger())
        self.assertTrue(result.enforcer_passed)
        self.assertEqual(len(result.violations), 0)


# ===========================================================================
# 9. Manufactured-probability source violation
# ===========================================================================

class TestManufacturedProbabilityViolation(unittest.TestCase):

    def test_l5_avg_derivation_fails(self):
        ledger = _complete_ledger()
        ledger["raw_probability_derivation"] = "L5_AVG"
        result = enforce(ledger)
        self.assertFalse(result.enforcer_passed)
        self.assertGreater(len(result.source_violations), 0)
        self.assertIn("ENFORCER_FAIL_MANUFACTURED_PROBABILITY", result.enforcement_code)

    def test_l10_avg_derivation_fails(self):
        ledger = _complete_ledger()
        ledger["raw_probability_derivation"] = "L10_AVG"
        result = enforce(ledger)
        self.assertFalse(result.enforcer_passed)

    def test_market_no_vig_derivation_fails(self):
        ledger = _complete_ledger()
        ledger["raw_probability_derivation"] = "MARKET_NO_VIG"
        result = enforce(ledger)
        self.assertFalse(result.enforcer_passed)

    def test_legitimate_model_derivation_does_not_fail(self):
        ledger = _complete_ledger()
        ledger["raw_probability_derivation"] = "REGISTERED_MODEL_v2"
        result = enforce(ledger)
        self.assertTrue(result.enforcer_passed)
        self.assertEqual(len(result.source_violations), 0)

    def test_no_derivation_field_does_not_fail(self):
        """Absence of raw_probability_derivation is not itself a violation."""
        ledger = _complete_ledger()
        ledger.pop("raw_probability_derivation", None)
        result = enforce(ledger)
        self.assertTrue(result.enforcer_passed)


# ===========================================================================
# 10. Governance invariants
# ===========================================================================

class TestGovernanceInvariants(unittest.TestCase):

    def test_enforcement_result_always_has_false_terminal_label_authority(self):
        for ledger in [_complete_ledger(), {}]:
            result = enforce(ledger)
            self.assertFalse(result.terminal_label_authority)

    def test_enforcement_result_always_has_false_can_execute(self):
        for ledger in [_complete_ledger(), {}]:
            result = enforce(ledger)
            self.assertFalse(result.can_execute)

    def test_module_level_constants(self):
        from gate_engine import prob_ledger_enforcer as mod
        self.assertFalse(mod.can_execute)
        self.assertFalse(mod.PRODUCTION_AUTHORITY)
        self.assertFalse(mod.USER_OUTPUT_AUTHORITY)

    def test_enforcement_result_is_frozen(self):
        result = enforce(_complete_ledger())
        with self.assertRaises((AttributeError, TypeError)):
            result.enforcer_passed = True  # type: ignore

    def test_enforce_never_raises(self):
        """enforce() must return an EnforcementResult even for pathological inputs."""
        bad_inputs = [None, 42, "string", [], object()]
        for bad in bad_inputs:
            try:
                result = enforce(bad)  # type: ignore
                self.assertIsInstance(result, EnforcementResult)
            except Exception as exc:
                self.fail(f"enforce({bad!r}) raised {type(exc).__name__}: {exc}")


if __name__ == "__main__":
    unittest.main()
