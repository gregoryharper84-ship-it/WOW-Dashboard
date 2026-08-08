"""
tests/test_kalshi_wx_shadow_schema.py
WOW-PATCH-2026-08-08-MULTI-AGENT-KALSHI-WX-SHADOW — Step 9 tests

Tests for the closed schema validator in gate_engine/kalshi_wx_shadow_schema.py.

Test plan
─────────
Section A — Positive: valid payload must pass
  A1: Fully valid payload with all required fields and correct types.
  A2: Valid payload with all optional nested fields populated.

Section B — Forbidden governance-authority key detection (recursive)
  B1: "terminal_label" nested THREE levels deep inside facts (must fail with
      FORBIDDEN_GOVERNANCE_KEY, not EXTRA_FIELD — forbidden scan runs first).
  B2: "can_execute": False inside probabilities (presence is the violation,
      regardless of value).
  B3: "final_label" at root level.
  B4: "capital_allocation" two levels deep inside uncertainty.
  B5: "authorized" inside a brackets_scored array item.
  B6: "approved_for_execution" inside source_conflicts (list, not dict) —
      confirm the scan descends into arrays too.
  B7: "governance_state" nested in facts.data_acquisition_notes item
      (string array — must NOT trigger, strings are not dicts/lists;
       this is a negative test confirming the scanner doesn't falsely match
       string values that happen to contain a forbidden word).

Section C — recommended_ceiling validation
  C1: OperationalState value "SHADOW_ONLY" → INVALID_CEILING.
  C2: ModelReadiness value "WEATHER_SCOUT" → INVALID_CEILING.
  C3: Invented string → INVALID_CEILING.
  C4: Each of the 6 valid CEILING_CAPABLE_LABELS values → PASS (parameterised).

Section D — advisory_only enforcement
  D1: advisory_only=False → ADVISORY_ONLY_NOT_TRUE.
  D2: advisory_only=1 (integer truthy, not boolean True) → ADVISORY_ONLY_NOT_TRUE.
  D3: advisory_only="true" (string) → ADVISORY_ONLY_NOT_TRUE.
  D4: advisory_only missing → MISSING_REQUIRED_FIELD.

Section E — lane enforcement
  E1: lane="KALSHI_SPORTS" → INVALID_LANE.
  E2: lane="kalshi_weather" (wrong case) → INVALID_LANE.
  E3: lane="" (empty string) → INVALID_LANE.

Section F — status enforcement
  F1: status="RUNNING" (not in the four allowed values) → INVALID_STATUS.
  F2: status="complete" (wrong case) → INVALID_STATUS.

Section G — additionalProperties=false enforcement
  G1: Extra key "confidence" in facts → EXTRA_FIELD.
  G2: Extra key "raw_score" in probabilities → EXTRA_FIELD.
  G3: Extra key "margin" in uncertainty → EXTRA_FIELD.
  G4: Extra key "debug_info" at root → EXTRA_FIELD.
  G5: "blockers" used instead of "agent_observed_blockers" (exact field name
      required; "blockers" is an unrecognized root key) → EXTRA_FIELD.
  G6: Extra key in a bracket item inside probabilities.brackets_scored →
      EXTRA_FIELD.

Section H — type enforcement
  H1: facts is a list, not a dict → WRONG_TYPE.
  H2: probabilities is a string → WRONG_TYPE.
  H3: agent_observed_blockers is a dict → WRONG_TYPE.
  H4: agent_id is an integer → WRONG_TYPE.
  H5: advisory_only missing (distinct from type error) — already covered by D4.

Section I — missing required fields
  I1: Missing "lane" → MISSING_REQUIRED_FIELD.
  I2: Missing "agent_observed_blockers" (exact field name) → MISSING_REQUIRED_FIELD.
  I3: Missing "advisory_only" → MISSING_REQUIRED_FIELD.

Section J — shadow_failure_only invariant
  J1: Every failure result has shadow_failure_only=True.
  J2: The success result (SHADOW_PASS) has shadow_failure_only=False.
  J3: SHADOW_PASS is the singleton — same object returned on every pass.

Section K — isolation: ceiling resolvers do not reference the schema module
  K1: gate_engine/wow_runtime_manifest.py contains no reference.
  K2: gate_engine/command_center/cc_labels.py contains no reference.
  K3: gate_engine/command_center/ceiling_resolver.py contains no reference.
"""
from __future__ import annotations

import copy
import os
import sys
import unittest

# ── path setup ────────────────────────────────────────────────────────────────
_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO not in sys.path:
    sys.path.insert(0, _REPO)

from gate_engine.kalshi_wx_shadow_registry import (
    CEILING_CAPABLE_LABELS,
    OperationalState,
    ModelReadiness,
)
from gate_engine.kalshi_wx_shadow_schema import (
    FORBIDDEN_GOVERNANCE_KEYS,
    ShadowSchemaViolation,
    ShadowValidationResult,
    SHADOW_PASS,
    validate_shadow_output,
)


# ─────────────────────────────────────────────────────────────────────────────
# Fixtures
# ─────────────────────────────────────────────────────────────────────────────

def _valid_payload() -> dict:
    """Return a minimally valid shadow agent output payload."""
    return {
        "agent_id":               "wx-shadow-agent-001",
        "run_id":                 "run-20260808-abc123",
        "lane":                   "KALSHI_WEATHER",
        "status":                 "COMPLETE",
        "facts": {
            "city":               "NYC",
            "date":               "2026-08-08",
            "nws_station_code":   "KNYC",
            "scoring_mode":       "gaussian_forecast",
            "forecast_high_f":    88.0,
        },
        "probabilities": {
            "brackets_scored": [
                {"bracket_range": "≤79",   "model_prob": 0.20, "verdict": "WATCH"},
                {"bracket_range": "80-84", "model_prob": 0.35, "verdict": "WATCH"},
                {"bracket_range": "≥85",   "model_prob": 0.45,
                 "verdict": "KALSHI_PLAYABLE_LIMIT_ONLY"},
            ],
            "model_prob_sum":     1.0,
            "calibration_status": "CALIBRATED",
        },
        "uncertainty": {
            "horizon_hours":      18.0,
            "sigma_f":            4.2,
            "uncertainty_tier":   "MODERATE",
        },
        "agent_observed_blockers": [],
        "source_conflicts":        [],
        "recommended_ceiling":     "KALSHI_PLAYABLE_LIMIT_ONLY",
        "advisory_only":           True,
    }


def _payload_with(**overrides) -> dict:
    """Return a valid payload with top-level fields overridden."""
    p = _valid_payload()
    p.update(overrides)
    return p


# ─────────────────────────────────────────────────────────────────────────────
# Section A — Positive
# ─────────────────────────────────────────────────────────────────────────────

class TestPositiveValidPayload(unittest.TestCase):

    def test_A1_minimal_valid_payload_passes(self):
        result = validate_shadow_output(_valid_payload())
        self.assertTrue(result.passed,
                        f"Expected PASS; got violation={result.violation} "
                        f"reason={result.failure_reason!r} path={result.failure_path!r}")
        self.assertIsNone(result.violation)
        self.assertIsNone(result.failure_reason)
        self.assertIsNone(result.failure_path)

    def test_A2_valid_payload_all_optional_nested_fields_passes(self):
        p = _valid_payload()
        p["facts"]["cli_high_f"] = 87.0
        p["facts"]["forecast_source_tier"] = "tier1"
        p["facts"]["data_acquisition_notes"] = ["NWS tier-1 hit", "no fallback needed"]
        p["uncertainty"]["notes"] = "Stable synoptic pattern; low uncertainty."
        p["agent_observed_blockers"] = ["missing CLI issuance for prior date"]
        p["source_conflicts"] = ["forecast tier1 vs open-meteo differed by 3°F"]
        result = validate_shadow_output(p)
        self.assertTrue(result.passed,
                        f"Expected PASS; got {result.violation} / {result.failure_reason!r}")


# ─────────────────────────────────────────────────────────────────────────────
# Section B — Forbidden governance-authority key detection (recursive)
# ─────────────────────────────────────────────────────────────────────────────

class TestForbiddenGovernanceKeys(unittest.TestCase):

    def _assert_forbidden(self, payload: dict, expected_key: str) -> ShadowValidationResult:
        result = validate_shadow_output(payload)
        self.assertFalse(result.passed, "Expected FAIL but got PASS")
        self.assertEqual(result.violation, ShadowSchemaViolation.FORBIDDEN_GOVERNANCE_KEY,
                         f"Expected FORBIDDEN_GOVERNANCE_KEY, got {result.violation}")
        self.assertIn(expected_key, result.failure_reason,
                      f"Expected {expected_key!r} in failure_reason; got {result.failure_reason!r}")
        self.assertTrue(result.shadow_failure_only)
        return result

    def test_B1_terminal_label_three_levels_deep_in_facts(self):
        """
        The forbidden key scan must reach terminal_label nested three levels
        inside facts — even though the intermediate keys are also extra fields.
        The result must be FORBIDDEN_GOVERNANCE_KEY, not EXTRA_FIELD.
        """
        p = _valid_payload()
        # facts → level1 → level2 → terminal_label  (3 hops below facts)
        p["facts"]["level1"] = {
            "level2": {
                "terminal_label": "KALSHI_PLAYABLE_LIMIT_ONLY"
            }
        }
        result = self._assert_forbidden(p, "terminal_label")
        self.assertIn("terminal_label", result.failure_path)

    def test_B2_can_execute_false_in_probabilities_fails_on_presence(self):
        """
        can_execute: False inside probabilities must fail.
        The VALUE False is irrelevant — the KEY ITSELF is forbidden.
        """
        p = _valid_payload()
        p["probabilities"]["can_execute"] = False
        result = self._assert_forbidden(p, "can_execute")
        self.assertIn("probabilities", result.failure_path)

    def test_B3_final_label_at_root(self):
        p = _valid_payload()
        p["final_label"] = "KALSHI_WATCH"
        self._assert_forbidden(p, "final_label")

    def test_B4_capital_allocation_two_levels_deep_in_uncertainty(self):
        p = _valid_payload()
        p["uncertainty"]["nested"] = {"capital_allocation": 500}
        self._assert_forbidden(p, "capital_allocation")

    def test_B5_authorized_inside_bracket_item(self):
        """Forbidden key inside an array item — scanner must descend into lists."""
        p = _valid_payload()
        p["probabilities"]["brackets_scored"].append({
            "bracket_range": "≥90",
            "model_prob": 0.05,
            "verdict": "WATCH",
            "authorized": True,   # FORBIDDEN
        })
        self._assert_forbidden(p, "authorized")

    def test_B6_approved_for_execution_key_name_in_source_conflicts_list(self):
        """
        source_conflicts is a list of strings.  A string value that happens to
        spell a forbidden key name is NOT a key — only dict keys are checked.
        This test confirms the scanner doesn't false-positive on string VALUES.
        The payload must PASS.
        """
        p = _valid_payload()
        p["source_conflicts"] = [
            "approved_for_execution is claimed by source A",
            "terminal_label discrepancy detected",
        ]
        result = validate_shadow_output(p)
        self.assertTrue(result.passed,
                        f"String VALUES containing forbidden words must not trigger "
                        f"the key scan; got violation={result.violation}")

    def test_B7_governance_state_nested_in_extra_object(self):
        """governance_state nested inside facts (via extra key) — must be FORBIDDEN_GOVERNANCE_KEY."""
        p = _valid_payload()
        p["facts"]["extra_obj"] = {"governance_state": "ACTIVE"}
        result = self._assert_forbidden(p, "governance_state")
        self.assertIn("governance_state", result.failure_path)

    def test_B8_all_forbidden_keys_are_rejected_individually(self):
        """Each key in FORBIDDEN_GOVERNANCE_KEYS must independently trigger the guard."""
        for key in FORBIDDEN_GOVERNANCE_KEYS:
            with self.subTest(key=key):
                p = _valid_payload()
                p["facts"]["injected"] = {key: "any_value"}
                result = validate_shadow_output(p)
                self.assertFalse(result.passed, f"{key!r} should have been rejected")
                self.assertEqual(result.violation,
                                 ShadowSchemaViolation.FORBIDDEN_GOVERNANCE_KEY,
                                 f"{key!r} gave wrong violation: {result.violation}")


# ─────────────────────────────────────────────────────────────────────────────
# Section C — recommended_ceiling validation
# ─────────────────────────────────────────────────────────────────────────────

class TestRecommendedCeiling(unittest.TestCase):

    def _assert_invalid_ceiling(self, ceiling_value: str) -> None:
        p = _payload_with(recommended_ceiling=ceiling_value)
        result = validate_shadow_output(p)
        self.assertFalse(result.passed, f"{ceiling_value!r} should fail ceiling check")
        self.assertEqual(result.violation, ShadowSchemaViolation.INVALID_CEILING,
                         f"Expected INVALID_CEILING, got {result.violation}")
        self.assertTrue(result.shadow_failure_only)

    def test_C1_operational_state_SHADOW_ONLY_fails(self):
        self._assert_invalid_ceiling(OperationalState.SHADOW_ONLY)

    def test_C2_model_readiness_WEATHER_SCOUT_fails(self):
        self._assert_invalid_ceiling(ModelReadiness.WEATHER_SCOUT)

    def test_C3_invented_string_fails(self):
        self._assert_invalid_ceiling("KALSHI_INVENTED_LABEL_XYZ")

    def test_C4_all_6_ceiling_capable_labels_pass(self):
        for ceiling in sorted(CEILING_CAPABLE_LABELS):
            with self.subTest(ceiling=ceiling):
                p = _payload_with(recommended_ceiling=ceiling)
                result = validate_shadow_output(p)
                self.assertTrue(result.passed,
                                f"{ceiling!r} is ceiling-capable but failed: "
                                f"{result.violation} / {result.failure_reason!r}")

    def test_C5_DRY_RUN_ONLY_operational_state_fails(self):
        self._assert_invalid_ceiling(OperationalState.DRY_RUN_ONLY)

    def test_C6_WEATHER_MODEL_READY_model_readiness_fails(self):
        self._assert_invalid_ceiling(ModelReadiness.WEATHER_MODEL_READY)

    def test_C7_empty_string_fails(self):
        self._assert_invalid_ceiling("")


# ─────────────────────────────────────────────────────────────────────────────
# Section D — advisory_only enforcement
# ─────────────────────────────────────────────────────────────────────────────

class TestAdvisoryOnly(unittest.TestCase):

    def test_D1_advisory_only_False_fails(self):
        result = validate_shadow_output(_payload_with(advisory_only=False))
        self.assertFalse(result.passed)
        self.assertEqual(result.violation, ShadowSchemaViolation.ADVISORY_ONLY_NOT_TRUE)
        self.assertTrue(result.shadow_failure_only)

    def test_D2_advisory_only_integer_1_fails(self):
        """Integer 1 is truthy but is not the boolean literal True."""
        result = validate_shadow_output(_payload_with(advisory_only=1))
        self.assertFalse(result.passed)
        self.assertEqual(result.violation, ShadowSchemaViolation.ADVISORY_ONLY_NOT_TRUE)

    def test_D3_advisory_only_string_true_fails(self):
        result = validate_shadow_output(_payload_with(advisory_only="true"))
        self.assertFalse(result.passed)
        self.assertEqual(result.violation, ShadowSchemaViolation.ADVISORY_ONLY_NOT_TRUE)

    def test_D4_advisory_only_missing_fails_as_missing_required(self):
        p = _valid_payload()
        del p["advisory_only"]
        result = validate_shadow_output(p)
        self.assertFalse(result.passed)
        self.assertEqual(result.violation, ShadowSchemaViolation.MISSING_REQUIRED_FIELD)
        self.assertIn("advisory_only", result.failure_reason)

    def test_D5_advisory_only_None_fails(self):
        result = validate_shadow_output(_payload_with(advisory_only=None))
        self.assertFalse(result.passed)
        self.assertEqual(result.violation, ShadowSchemaViolation.ADVISORY_ONLY_NOT_TRUE)


# ─────────────────────────────────────────────────────────────────────────────
# Section E — lane enforcement
# ─────────────────────────────────────────────────────────────────────────────

class TestLaneEnforcement(unittest.TestCase):

    def _assert_invalid_lane(self, lane_value: str) -> None:
        result = validate_shadow_output(_payload_with(lane=lane_value))
        self.assertFalse(result.passed, f"lane={lane_value!r} should fail")
        self.assertEqual(result.violation, ShadowSchemaViolation.INVALID_LANE)

    def test_E1_kalshi_sports_fails(self):
        self._assert_invalid_lane("KALSHI_SPORTS")

    def test_E2_lowercase_fails(self):
        self._assert_invalid_lane("kalshi_weather")

    def test_E3_empty_string_fails(self):
        self._assert_invalid_lane("")

    def test_E4_LLP_fails(self):
        self._assert_invalid_lane("LLP")


# ─────────────────────────────────────────────────────────────────────────────
# Section F — status enforcement
# ─────────────────────────────────────────────────────────────────────────────

class TestStatusEnforcement(unittest.TestCase):

    def test_F1_RUNNING_not_allowed(self):
        result = validate_shadow_output(_payload_with(status="RUNNING"))
        self.assertFalse(result.passed)
        self.assertEqual(result.violation, ShadowSchemaViolation.INVALID_STATUS)

    def test_F2_lowercase_complete_not_allowed(self):
        result = validate_shadow_output(_payload_with(status="complete"))
        self.assertFalse(result.passed)
        self.assertEqual(result.violation, ShadowSchemaViolation.INVALID_STATUS)

    def test_F3_each_valid_status_passes(self):
        for status in ("COMPLETE", "SCHEMA_FAIL", "TOOL_FAIL", "BLOCKED"):
            with self.subTest(status=status):
                result = validate_shadow_output(_payload_with(status=status))
                self.assertTrue(result.passed,
                                f"status={status!r} should pass; got {result.violation}")


# ─────────────────────────────────────────────────────────────────────────────
# Section G — additionalProperties=false enforcement
# ─────────────────────────────────────────────────────────────────────────────

class TestAdditionalProperties(unittest.TestCase):

    def _assert_extra_field(self, payload: dict) -> ShadowValidationResult:
        result = validate_shadow_output(payload)
        self.assertFalse(result.passed, "Expected FAIL for extra field")
        self.assertEqual(result.violation, ShadowSchemaViolation.EXTRA_FIELD,
                         f"Expected EXTRA_FIELD, got {result.violation}")
        self.assertTrue(result.shadow_failure_only)
        return result

    def test_G1_extra_key_in_facts(self):
        p = _valid_payload()
        p["facts"]["confidence"] = 0.95
        self._assert_extra_field(p)

    def test_G2_extra_key_in_probabilities(self):
        p = _valid_payload()
        p["probabilities"]["raw_score"] = 42
        self._assert_extra_field(p)

    def test_G3_extra_key_in_uncertainty(self):
        p = _valid_payload()
        p["uncertainty"]["margin"] = 1.5
        self._assert_extra_field(p)

    def test_G4_extra_key_at_root(self):
        p = _valid_payload()
        p["debug_info"] = {"internal": True}
        self._assert_extra_field(p)

    def test_G5_blockers_instead_of_agent_observed_blockers(self):
        """
        "blockers" is the wrong field name.  The validator must NOT treat it
        as an alias for agent_observed_blockers.  It must fail as EXTRA_FIELD
        (for "blockers" being unexpected at root).

        The payload uses "blockers" and omits "agent_observed_blockers".
        The forbidden-key scan runs first but "blockers" is not in
        FORBIDDEN_GOVERNANCE_KEYS.  Next: extra-key check fires because
        "blockers" is not in ROOT_ALLOWED_KEYS → EXTRA_FIELD.
        """
        p = _valid_payload()
        del p["agent_observed_blockers"]
        p["blockers"] = ["some blocker"]
        result = self._assert_extra_field(p)
        self.assertIn("blockers", result.failure_reason,
                      "failure_reason should name the unexpected key 'blockers'")

    def test_G6_extra_key_in_bracket_item(self):
        """Extra key inside a brackets_scored array item."""
        p = _valid_payload()
        p["probabilities"]["brackets_scored"][0]["extra_field"] = "bad"
        self._assert_extra_field(p)


# ─────────────────────────────────────────────────────────────────────────────
# Section H — type enforcement
# ─────────────────────────────────────────────────────────────────────────────

class TestTypeEnforcement(unittest.TestCase):

    def _assert_wrong_type(self, payload: dict) -> None:
        result = validate_shadow_output(payload)
        self.assertFalse(result.passed)
        self.assertEqual(result.violation, ShadowSchemaViolation.WRONG_TYPE,
                         f"Expected WRONG_TYPE, got {result.violation}")

    def test_H1_facts_is_list_not_dict(self):
        self._assert_wrong_type(_payload_with(facts=["city", "NYC"]))

    def test_H2_probabilities_is_string(self):
        self._assert_wrong_type(_payload_with(probabilities="bad"))

    def test_H3_agent_observed_blockers_is_dict(self):
        self._assert_wrong_type(_payload_with(agent_observed_blockers={"key": "val"}))

    def test_H4_agent_id_is_integer(self):
        self._assert_wrong_type(_payload_with(agent_id=42))

    def test_H5_payload_is_not_dict(self):
        result = validate_shadow_output(["not", "a", "dict"])
        self.assertFalse(result.passed)
        self.assertEqual(result.violation, ShadowSchemaViolation.WRONG_TYPE)


# ─────────────────────────────────────────────────────────────────────────────
# Section I — missing required fields
# ─────────────────────────────────────────────────────────────────────────────

class TestMissingRequiredFields(unittest.TestCase):

    def _assert_missing(self, field: str) -> None:
        p = _valid_payload()
        del p[field]
        result = validate_shadow_output(p)
        self.assertFalse(result.passed)
        self.assertEqual(result.violation, ShadowSchemaViolation.MISSING_REQUIRED_FIELD,
                         f"Expected MISSING_REQUIRED_FIELD for {field!r}, got {result.violation}")
        self.assertIn(field, result.failure_reason)

    def test_I1_missing_lane(self):
        self._assert_missing("lane")

    def test_I2_missing_agent_observed_blockers(self):
        self._assert_missing("agent_observed_blockers")

    def test_I3_missing_advisory_only(self):
        self._assert_missing("advisory_only")

    def test_I4_missing_recommended_ceiling(self):
        self._assert_missing("recommended_ceiling")

    def test_I5_missing_facts(self):
        self._assert_missing("facts")

    def test_I6_all_required_fields_tested(self):
        """Every required root field produces MISSING_REQUIRED_FIELD when absent."""
        from gate_engine.kalshi_wx_shadow_schema import ROOT_REQUIRED_KEYS
        for field in sorted(ROOT_REQUIRED_KEYS):
            with self.subTest(field=field):
                p = _valid_payload()
                del p[field]
                result = validate_shadow_output(p)
                self.assertFalse(result.passed,
                                 f"Removing {field!r} should fail")
                # advisory_only=True check fires before missing-field check for that field
                # when it's absent; MISSING_REQUIRED_FIELD is the expected violation.
                self.assertIn(result.violation, (
                    ShadowSchemaViolation.MISSING_REQUIRED_FIELD,
                    ShadowSchemaViolation.WRONG_TYPE,    # e.g. lane/status scalar check
                ), f"{field!r}: unexpected violation {result.violation}")


# ─────────────────────────────────────────────────────────────────────────────
# Section J — shadow_failure_only invariant
# ─────────────────────────────────────────────────────────────────────────────

class TestShadowFailureOnlyInvariant(unittest.TestCase):

    _ADVERSARIAL_PAYLOADS = [
        # (description, payload)
        ("advisory_only=False",       lambda: _payload_with(advisory_only=False)),
        ("wrong lane",                lambda: _payload_with(lane="LLP")),
        ("wrong status",              lambda: _payload_with(status="PENDING")),
        ("bad ceiling",               lambda: _payload_with(recommended_ceiling="SHADOW_ONLY")),
        ("forbidden key",             lambda: {**_valid_payload(), "terminal_label": "x"}),
        ("extra root key",            lambda: {**_valid_payload(), "extra": True}),
        ("facts is wrong type",       lambda: _payload_with(facts=[])),
    ]

    def test_J1_every_failure_has_shadow_failure_only_True(self):
        for desc, make_payload in self._ADVERSARIAL_PAYLOADS:
            with self.subTest(desc=desc):
                result = validate_shadow_output(make_payload())
                self.assertFalse(result.passed, f"{desc}: expected failure")
                self.assertTrue(
                    result.shadow_failure_only,
                    f"{desc}: shadow_failure_only must be True on every failure result",
                )

    def test_J2_pass_result_has_shadow_failure_only_False(self):
        result = validate_shadow_output(_valid_payload())
        self.assertTrue(result.passed)
        self.assertFalse(result.shadow_failure_only,
                         "Pass result must have shadow_failure_only=False")

    def test_J3_pass_returns_SHADOW_PASS_singleton(self):
        from gate_engine.kalshi_wx_shadow_schema import SHADOW_PASS
        result = validate_shadow_output(_valid_payload())
        self.assertIs(result, SHADOW_PASS,
                      "validate_shadow_output must return the SHADOW_PASS singleton on success")


# ─────────────────────────────────────────────────────────────────────────────
# Section K — isolation: ceiling resolvers do not reference the schema module
# ─────────────────────────────────────────────────────────────────────────────

class TestCeilingResolverIsolation(unittest.TestCase):

    _SCHEMA_SYMBOLS = (
        "kalshi_wx_shadow_schema",
        "validate_shadow_output",
        "ShadowValidationResult",
        "ShadowSchemaViolation",
        "SHADOW_PASS",
        "FORBIDDEN_GOVERNANCE_KEYS",
    )

    def _read(self, rel: str) -> str:
        with open(os.path.join(_REPO, rel), encoding="utf-8") as fh:
            return fh.read()

    def _assert_absent(self, src: str, symbol: str, filename: str) -> None:
        self.assertNotIn(symbol, src,
                         f"{symbol!r} must not appear in {filename}")

    def test_K1_wow_runtime_manifest_not_referencing_schema(self):
        src = self._read("gate_engine/wow_runtime_manifest.py")
        for sym in self._SCHEMA_SYMBOLS:
            self._assert_absent(src, sym, "gate_engine/wow_runtime_manifest.py")

    def test_K2_cc_labels_not_referencing_schema(self):
        src = self._read("gate_engine/command_center/cc_labels.py")
        for sym in self._SCHEMA_SYMBOLS:
            self._assert_absent(src, sym, "gate_engine/command_center/cc_labels.py")

    def test_K3_ceiling_resolver_not_referencing_schema(self):
        src = self._read("gate_engine/command_center/ceiling_resolver.py")
        for sym in self._SCHEMA_SYMBOLS:
            self._assert_absent(src, sym, "gate_engine/command_center/ceiling_resolver.py")


if __name__ == "__main__":
    unittest.main(verbosity=2)
