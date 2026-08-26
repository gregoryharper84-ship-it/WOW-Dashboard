"""
tests/test_kalshi_wx_shadow_native_schema.py
Step 14C — Adversarial tests for the native per-subagent closed-schema validators.

PART D coverage:
  - final_decision key  → rejected (unknown property)
  - stake_tier key      → rejected (unknown property)
  - is_playable key     → rejected (unknown property)
  - arbitrary unrecognised nested field → rejected
  - wrong-typed field (string where bool required, etc.) → rejected
  - missing required field → rejected

  Each violation is tested against at least 2 different subagents.

Also covers positive (valid) paths so regressions are visible.

Zero real Anthropic API calls.  Pure in-process logic tests.
"""
from __future__ import annotations

import unittest

from gate_engine.kalshi_wx_shadow_native_schema import (
    validate_contradiction_detection,
    validate_forecast_context,
    validate_source_reconciliation,
    validate_subagent_output,
    validate_uncertainty_explanation,
    validate_unusual_regime,
)


# ── Valid fixture builders ────────────────────────────────────────────────────

def _valid_fc() -> dict:
    return {
        "scoring_mode":       "gaussian_forecast",
        "calibration_status": "PROVISIONAL",
        "uncertainty_tier":   "MODERATE",
        "recommended_ceiling": "KALSHI_WATCH",
        "blockers":           [],
    }


def _valid_sr() -> dict:
    return {
        "sources_present":      ["nws_primary"],
        "sources_missing":      ["open_meteo"],
        "conflicts":            [],
        "reconciliation_status": "PARTIAL",
    }


def _valid_cd() -> dict:
    return {
        "contradictions_found": [],
        "ceiling_impacted":     False,
    }


def _valid_ur() -> dict:
    return {
        "regime_unusual":    False,
        "regime_factors":    [],
        "reliability_impact": "NONE",
    }


def _valid_ue() -> dict:
    return {
        "uncertainty_tier":   "MODERATE",
        "uncertainty_sources": ["forecast_horizon"],
        "ceiling_impact":     "MINOR",
    }


# ═════════════════════════════════════════════════════════════════════════════
# T1: Valid inputs pass for all 5 subagents
# ═════════════════════════════════════════════════════════════════════════════

class TestValidInputsPass(unittest.TestCase):

    def test_forecast_context_minimal_valid(self):
        ok, reason = validate_forecast_context(_valid_fc())
        self.assertTrue(ok, reason)

    def test_forecast_context_with_optional_notes(self):
        d = {**_valid_fc(), "notes": "This is a note."}
        ok, reason = validate_forecast_context(d)
        self.assertTrue(ok, reason)

    def test_forecast_context_all_enum_values(self):
        for sm in ("gaussian_forecast", "binary_final_cli"):
            for cs in ("CALIBRATED", "PROVISIONAL", "UNAVAILABLE"):
                for ut in ("LOW", "MODERATE", "HIGH"):
                    d = {**_valid_fc(), "scoring_mode": sm,
                         "calibration_status": cs, "uncertainty_tier": ut}
                    ok, reason = validate_forecast_context(d)
                    self.assertTrue(ok, f"sm={sm} cs={cs} ut={ut}: {reason}")

    def test_forecast_context_all_ceiling_values(self):
        for ceiling in (
            "KALSHI_WATCH", "KALSHI_PLAYABLE_LIMIT_ONLY",
            "KALSHI_REJECT_BAD_RULES", "KALSHI_REJECT_NO_EDGE",
            "KALSHI_DATA_UNOBTAINABLE",
        ):
            d = {**_valid_fc(), "recommended_ceiling": ceiling}
            ok, reason = validate_forecast_context(d)
            self.assertTrue(ok, f"ceiling={ceiling}: {reason}")

    def test_source_reconciliation_valid(self):
        ok, reason = validate_source_reconciliation(_valid_sr())
        self.assertTrue(ok, reason)

    def test_contradiction_detection_valid_no_impact(self):
        ok, reason = validate_contradiction_detection(_valid_cd())
        self.assertTrue(ok, reason)

    def test_contradiction_detection_valid_with_revised_ceiling(self):
        d = {**_valid_cd(),
             "ceiling_impacted": True,
             "revised_ceiling":  "KALSHI_REJECT_NO_EDGE"}
        ok, reason = validate_contradiction_detection(d)
        self.assertTrue(ok, reason)

    def test_unusual_regime_valid(self):
        ok, reason = validate_unusual_regime(_valid_ur())
        self.assertTrue(ok, reason)

    def test_uncertainty_explanation_valid(self):
        ok, reason = validate_uncertainty_explanation(_valid_ue())
        self.assertTrue(ok, reason)

    def test_uncertainty_explanation_with_numeric_optionals(self):
        d = {**_valid_ue(), "sigma_f_estimate": 3.5, "horizon_hours_estimate": 9.2}
        ok, reason = validate_uncertainty_explanation(d)
        self.assertTrue(ok, reason)

    def test_dispatcher_routes_correctly(self):
        for sid, fixture in (
            ("forecast_context",        _valid_fc()),
            ("source_reconciliation",   _valid_sr()),
            ("contradiction_detection", _valid_cd()),
            ("unusual_regime",          _valid_ur()),
            ("uncertainty_explanation", _valid_ue()),
        ):
            ok, reason = validate_subagent_output(sid, fixture)
            self.assertTrue(ok, f"{sid}: {reason}")


# ═════════════════════════════════════════════════════════════════════════════
# T2: final_decision key — must be rejected (not in FORBIDDEN_GOVERNANCE_KEYS
#     but not in any allowed-key set either)
# ═════════════════════════════════════════════════════════════════════════════

class TestFinalDecisionKeyRejected(unittest.TestCase):
    """
    final_decision is NOT in FORBIDDEN_GOVERNANCE_KEYS (so CapabilityBoundary
    does not catch it), but it IS rejected by the native additionalProperties=false
    enforcement.
    """

    def test_forecast_context_rejects_final_decision(self):
        d = {**_valid_fc(), "final_decision": "APPROVED"}
        ok, reason = validate_forecast_context(d)
        self.assertFalse(ok, "expected failure but got pass")
        self.assertIn("final_decision", reason)
        self.assertIn("unknown", reason.lower())

    def test_source_reconciliation_rejects_final_decision(self):
        d = {**_valid_sr(), "final_decision": "WATCH"}
        ok, reason = validate_source_reconciliation(d)
        self.assertFalse(ok, "expected failure but got pass")
        self.assertIn("final_decision", reason)

    def test_contradiction_detection_rejects_final_decision(self):
        d = {**_valid_cd(), "final_decision": "REJECT"}
        ok, reason = validate_contradiction_detection(d)
        self.assertFalse(ok)
        self.assertIn("final_decision", reason)

    def test_dispatcher_propagates_final_decision_rejection(self):
        d = {**_valid_ue(), "final_decision": "NO_PLAY"}
        ok, reason = validate_subagent_output("uncertainty_explanation", d)
        self.assertFalse(ok)
        self.assertIn("final_decision", reason)


# ═════════════════════════════════════════════════════════════════════════════
# T3: stake_tier key — must be rejected
# ═════════════════════════════════════════════════════════════════════════════

class TestStakeTierKeyRejected(unittest.TestCase):

    def test_forecast_context_rejects_stake_tier(self):
        d = {**_valid_fc(), "stake_tier": "STANDARD"}
        ok, reason = validate_forecast_context(d)
        self.assertFalse(ok)
        self.assertIn("stake_tier", reason)

    def test_unusual_regime_rejects_stake_tier(self):
        d = {**_valid_ur(), "stake_tier": "HIGH"}
        ok, reason = validate_unusual_regime(d)
        self.assertFalse(ok)
        self.assertIn("stake_tier", reason)

    def test_uncertainty_explanation_rejects_stake_tier(self):
        d = {**_valid_ue(), "stake_tier": "PASS"}
        ok, reason = validate_uncertainty_explanation(d)
        self.assertFalse(ok)
        self.assertIn("stake_tier", reason)


# ═════════════════════════════════════════════════════════════════════════════
# T4: is_playable key — must be rejected
# ═════════════════════════════════════════════════════════════════════════════

class TestIsPlayableKeyRejected(unittest.TestCase):

    def test_forecast_context_rejects_is_playable(self):
        d = {**_valid_fc(), "is_playable": True}
        ok, reason = validate_forecast_context(d)
        self.assertFalse(ok)
        self.assertIn("is_playable", reason)

    def test_source_reconciliation_rejects_is_playable(self):
        d = {**_valid_sr(), "is_playable": False}
        ok, reason = validate_source_reconciliation(d)
        self.assertFalse(ok)
        self.assertIn("is_playable", reason)

    def test_contradiction_detection_rejects_is_playable(self):
        d = {**_valid_cd(), "is_playable": True}
        ok, reason = validate_contradiction_detection(d)
        self.assertFalse(ok)
        self.assertIn("is_playable", reason)

    def test_dispatcher_propagates_is_playable_rejection(self):
        d = {**_valid_ur(), "is_playable": "yes"}
        ok, reason = validate_subagent_output("unusual_regime", d)
        self.assertFalse(ok)
        self.assertIn("is_playable", reason)


# ═════════════════════════════════════════════════════════════════════════════
# T5: Arbitrary unrecognised fields — must be rejected
# ═════════════════════════════════════════════════════════════════════════════

class TestArbitraryUnknownFieldsRejected(unittest.TestCase):

    def test_fc_rejects_arbitrary_top_level_key(self):
        d = {**_valid_fc(), "some_random_field": "value"}
        ok, reason = validate_forecast_context(d)
        self.assertFalse(ok)
        self.assertIn("some_random_field", reason)

    def test_sr_rejects_multiple_unknown_keys(self):
        d = {**_valid_sr(), "alpha": 1, "beta": 2}
        ok, reason = validate_source_reconciliation(d)
        self.assertFalse(ok)
        # Both unknown keys should appear in the reason
        self.assertIn("alpha", reason)

    def test_cd_rejects_approved_field(self):
        d = {**_valid_cd(), "approved": True}
        ok, reason = validate_contradiction_detection(d)
        self.assertFalse(ok)
        self.assertIn("approved", reason)

    def test_ue_rejects_confidence_score_field(self):
        # confidence_score sounds legitimate but is not in the schema
        d = {**_valid_ue(), "confidence_score": 0.92}
        ok, reason = validate_uncertainty_explanation(d)
        self.assertFalse(ok)
        self.assertIn("confidence_score", reason)

    def test_ur_rejects_execution_flag(self):
        d = {**_valid_ur(), "execute_trade": False}
        ok, reason = validate_unusual_regime(d)
        self.assertFalse(ok)
        self.assertIn("execute_trade", reason)


# ═════════════════════════════════════════════════════════════════════════════
# T6: Wrong-typed fields — must be rejected with clear type error message
# ═════════════════════════════════════════════════════════════════════════════

class TestWrongTypedFieldsRejected(unittest.TestCase):

    # forecast_context: ceiling_impacted doesn't exist here, test blockers type
    def test_fc_rejects_string_blockers(self):
        """blockers must be array; string is wrong type"""
        d = {**_valid_fc(), "blockers": "none"}
        ok, reason = validate_forecast_context(d)
        self.assertFalse(ok)
        self.assertIn("blockers", reason)
        self.assertIn("array", reason.lower())

    def test_fc_rejects_integer_scoring_mode(self):
        d = {**_valid_fc(), "scoring_mode": 1}
        ok, reason = validate_forecast_context(d)
        self.assertFalse(ok)
        self.assertIn("scoring_mode", reason)
        self.assertIn("string", reason.lower())

    def test_cd_rejects_string_where_bool_required(self):
        """ceiling_impacted must be bool; string 'true' is wrong type"""
        d = {**_valid_cd(), "ceiling_impacted": "true"}
        ok, reason = validate_contradiction_detection(d)
        self.assertFalse(ok)
        self.assertIn("ceiling_impacted", reason)
        self.assertIn("bool", reason.lower())

    def test_cd_rejects_integer_where_bool_required(self):
        """int 1 is not bool (Python int is not bool)"""
        d = {**_valid_cd(), "ceiling_impacted": 1}
        ok, reason = validate_contradiction_detection(d)
        self.assertFalse(ok)
        self.assertIn("ceiling_impacted", reason)

    def test_ur_rejects_string_regime_unusual(self):
        """regime_unusual must be bool; 'false' (string) is wrong"""
        d = {**_valid_ur(), "regime_unusual": "false"}
        ok, reason = validate_unusual_regime(d)
        self.assertFalse(ok)
        self.assertIn("regime_unusual", reason)
        self.assertIn("bool", reason.lower())

    def test_ue_rejects_string_sigma_f(self):
        """sigma_f_estimate must be a number; string is wrong"""
        d = {**_valid_ue(), "sigma_f_estimate": "3.5"}
        ok, reason = validate_uncertainty_explanation(d)
        self.assertFalse(ok)
        self.assertIn("sigma_f_estimate", reason)
        self.assertIn("number", reason.lower())

    def test_ue_rejects_bool_sigma_f(self):
        """sigma_f_estimate must be a number; True/False are bools, not numbers"""
        d = {**_valid_ue(), "sigma_f_estimate": True}
        ok, reason = validate_uncertainty_explanation(d)
        self.assertFalse(ok)
        self.assertIn("sigma_f_estimate", reason)

    def test_sr_rejects_dict_sources_present(self):
        """sources_present must be array; dict is wrong"""
        d = {**_valid_sr(), "sources_present": {"nws": True}}
        ok, reason = validate_source_reconciliation(d)
        self.assertFalse(ok)
        self.assertIn("sources_present", reason)
        self.assertIn("array", reason.lower())

    def test_sr_rejects_array_of_non_strings(self):
        """each element of sources_present must be a string"""
        d = {**_valid_sr(), "sources_present": [1, 2, 3]}
        ok, reason = validate_source_reconciliation(d)
        self.assertFalse(ok)
        self.assertIn("sources_present", reason)
        self.assertIn("string", reason.lower())


# ═════════════════════════════════════════════════════════════════════════════
# T7: Missing required fields — must be rejected with clear message
# ═════════════════════════════════════════════════════════════════════════════

class TestMissingRequiredFieldsRejected(unittest.TestCase):

    def test_fc_missing_scoring_mode(self):
        d = {k: v for k, v in _valid_fc().items() if k != "scoring_mode"}
        ok, reason = validate_forecast_context(d)
        self.assertFalse(ok)
        self.assertIn("scoring_mode", reason)
        self.assertIn("missing", reason.lower())

    def test_fc_missing_recommended_ceiling(self):
        d = {k: v for k, v in _valid_fc().items() if k != "recommended_ceiling"}
        ok, reason = validate_forecast_context(d)
        self.assertFalse(ok)
        self.assertIn("recommended_ceiling", reason)

    def test_fc_missing_blockers(self):
        d = {k: v for k, v in _valid_fc().items() if k != "blockers"}
        ok, reason = validate_forecast_context(d)
        self.assertFalse(ok)
        self.assertIn("blockers", reason)

    def test_sr_missing_reconciliation_status(self):
        d = {k: v for k, v in _valid_sr().items() if k != "reconciliation_status"}
        ok, reason = validate_source_reconciliation(d)
        self.assertFalse(ok)
        self.assertIn("reconciliation_status", reason)

    def test_cd_missing_contradictions_found(self):
        d = {k: v for k, v in _valid_cd().items() if k != "contradictions_found"}
        ok, reason = validate_contradiction_detection(d)
        self.assertFalse(ok)
        self.assertIn("contradictions_found", reason)

    def test_cd_missing_ceiling_impacted(self):
        d = {k: v for k, v in _valid_cd().items() if k != "ceiling_impacted"}
        ok, reason = validate_contradiction_detection(d)
        self.assertFalse(ok)
        self.assertIn("ceiling_impacted", reason)

    def test_ur_missing_regime_unusual(self):
        d = {k: v for k, v in _valid_ur().items() if k != "regime_unusual"}
        ok, reason = validate_unusual_regime(d)
        self.assertFalse(ok)
        self.assertIn("regime_unusual", reason)

    def test_ur_missing_reliability_impact(self):
        d = {k: v for k, v in _valid_ur().items() if k != "reliability_impact"}
        ok, reason = validate_unusual_regime(d)
        self.assertFalse(ok)
        self.assertIn("reliability_impact", reason)

    def test_ue_missing_uncertainty_tier(self):
        d = {k: v for k, v in _valid_ue().items() if k != "uncertainty_tier"}
        ok, reason = validate_uncertainty_explanation(d)
        self.assertFalse(ok)
        self.assertIn("uncertainty_tier", reason)

    def test_ue_missing_ceiling_impact(self):
        d = {k: v for k, v in _valid_ue().items() if k != "ceiling_impact"}
        ok, reason = validate_uncertainty_explanation(d)
        self.assertFalse(ok)
        self.assertIn("ceiling_impact", reason)

    def test_empty_dict_fails_all_subagents(self):
        for sid in (
            "forecast_context", "source_reconciliation",
            "contradiction_detection", "unusual_regime",
            "uncertainty_explanation",
        ):
            ok, reason = validate_subagent_output(sid, {})
            self.assertFalse(ok, f"{sid} should fail on empty dict")
            self.assertIn("missing", reason.lower())


# ═════════════════════════════════════════════════════════════════════════════
# T8: Invalid enum values — must be rejected
# ═════════════════════════════════════════════════════════════════════════════

class TestInvalidEnumValuesRejected(unittest.TestCase):

    def test_fc_invalid_scoring_mode(self):
        d = {**_valid_fc(), "scoring_mode": "DETERMINISTIC"}
        ok, reason = validate_forecast_context(d)
        self.assertFalse(ok)
        self.assertIn("scoring_mode", reason)
        self.assertIn("DETERMINISTIC", reason)

    def test_fc_invalid_calibration_status(self):
        d = {**_valid_fc(), "calibration_status": "CONFIRMED"}
        ok, reason = validate_forecast_context(d)
        self.assertFalse(ok)
        self.assertIn("calibration_status", reason)

    def test_fc_invalid_recommended_ceiling(self):
        """An invented ceiling value not in the registry must fail."""
        d = {**_valid_fc(), "recommended_ceiling": "KALSHI_REJECT_UNCALIBRATED"}
        ok, reason = validate_forecast_context(d)
        self.assertFalse(ok)
        self.assertIn("recommended_ceiling", reason)

    def test_cd_invalid_revised_ceiling(self):
        d = {**_valid_cd(),
             "ceiling_impacted": True,
             "revised_ceiling":  "PLAY_IT_ANYWAY"}
        ok, reason = validate_contradiction_detection(d)
        self.assertFalse(ok)
        self.assertIn("revised_ceiling", reason)

    def test_sr_invalid_reconciliation_status(self):
        d = {**_valid_sr(), "reconciliation_status": "UNKNOWN"}
        ok, reason = validate_source_reconciliation(d)
        self.assertFalse(ok)
        self.assertIn("reconciliation_status", reason)

    def test_ur_invalid_reliability_impact(self):
        d = {**_valid_ur(), "reliability_impact": "CATASTROPHIC"}
        ok, reason = validate_unusual_regime(d)
        self.assertFalse(ok)
        self.assertIn("reliability_impact", reason)

    def test_ue_invalid_ceiling_impact(self):
        d = {**_valid_ue(), "ceiling_impact": "EXTREME"}
        ok, reason = validate_uncertainty_explanation(d)
        self.assertFalse(ok)
        self.assertIn("ceiling_impact", reason)


# ═════════════════════════════════════════════════════════════════════════════
# T9: Dispatcher edge cases
# ═════════════════════════════════════════════════════════════════════════════

class TestDispatcherEdgeCases(unittest.TestCase):

    def test_unknown_subagent_id_returns_false(self):
        ok, reason = validate_subagent_output("nonexistent_agent", {"foo": "bar"})
        self.assertFalse(ok)
        self.assertIn("UNKNOWN_SUBAGENT_ID", reason)
        self.assertIn("nonexistent_agent", reason)

    def test_non_dict_tool_input_rejected(self):
        for sid in (
            "forecast_context", "source_reconciliation",
            "contradiction_detection", "unusual_regime",
            "uncertainty_explanation",
        ):
            ok, reason = validate_subagent_output(sid, "not a dict")
            self.assertFalse(ok, f"{sid} should reject non-dict")

    def test_none_tool_input_rejected(self):
        ok, reason = validate_subagent_output("forecast_context", None)
        self.assertFalse(ok)

    def test_list_tool_input_rejected(self):
        ok, reason = validate_subagent_output("unusual_regime", [1, 2, 3])
        self.assertFalse(ok)

    def test_all_three_governance_like_keys_rejected_in_fc(self):
        """final_decision + stake_tier + is_playable all in one dict → rejected."""
        d = {
            **_valid_fc(),
            "final_decision": "APPROVED",
            "stake_tier": "STANDARD",
            "is_playable": True,
        }
        ok, reason = validate_forecast_context(d)
        self.assertFalse(ok)
        # At least one of the injected keys must appear in the reason
        self.assertTrue(
            any(k in reason for k in ("final_decision", "stake_tier", "is_playable")),
            f"Expected one of the governance-like keys in reason: {reason}",
        )

    def test_all_three_governance_like_keys_rejected_in_ue(self):
        d = {
            **_valid_ue(),
            "final_decision": "NO_PLAY",
            "stake_tier": "PASS",
            "is_playable": False,
        }
        ok, reason = validate_uncertainty_explanation(d)
        self.assertFalse(ok)
        self.assertTrue(
            any(k in reason for k in ("final_decision", "stake_tier", "is_playable")),
        )


if __name__ == "__main__":
    unittest.main()
