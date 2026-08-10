"""
tests/test_universal_agent_b1.py
WOW-PATCH-2026-08-09-UNIVERSAL-AGENT-CORE-V1 / Phase B1

Unit tests for the six universal advisory role contracts.

Coverage:
  T1  RoleBase — shared validator infrastructure
  T2  Data/Slate Integrity role — schema + adversarial
  T3  News/Status role — schema + adversarial
  T4  Market/Exact-Line role — schema + adversarial
  T5  Sport Specialist role — schema + adversarial
  T6  Failure/Contradiction role — schema + adversarial
  T7  Final Refresh role — schema + adversarial
  T8  B1 Registry — duplicate/unknown role handling (fail-closed)
  T9  B0 Enforcement Sharing — proves role validators invoke B0 scanner

All tests are pure unit tests (no DB, no network, no LLM calls, no app.py).
No test modifies any module-level singleton.
"""
from __future__ import annotations

import unittest
from typing import Any

# ── B0 imports (for assertIs comparisons) ─────────────────────────────────────
from gate_engine.universal_agent.output_contract import (
    OUTPUT_VALID,
    OutputViolationCode,
    _scan_forbidden_keys as _B0_SCAN_FORBIDDEN,
    validate_output_contract as _B0_VALIDATE,
    valid_output_payload,
)
from gate_engine.universal_agent.agent_registry import AgentRegistry

# ── B1 imports ─────────────────────────────────────────────────────────────────
from gate_engine.universal_agent.roles.role_base import (
    SCHEMA_VERSION,
    RoleViolationCode,
    EvidenceAvailability,
    _scan_forbidden_keys as _B1_SCAN_FORBIDDEN,
    validate_output_contract as _B1_VALIDATE,
)
from gate_engine.universal_agent.roles.data_slate_integrity import (
    ROLE_ID as DSI_ROLE_ID,
    validate_data_slate_integrity_output as validate_dsi,
    valid_data_slate_integrity_payload as dsi_payload,
    REGISTRY_ENTRY as DSI_ENTRY,
)
from gate_engine.universal_agent.roles.news_status import (
    ROLE_ID as NS_ROLE_ID,
    validate_news_status_output as validate_ns,
    valid_news_status_payload as ns_payload,
    REGISTRY_ENTRY as NS_ENTRY,
)
from gate_engine.universal_agent.roles.market_exact_line import (
    ROLE_ID as MEL_ROLE_ID,
    validate_market_exact_line_output as validate_mel,
    valid_market_exact_line_payload as mel_payload,
    REGISTRY_ENTRY as MEL_ENTRY,
)
from gate_engine.universal_agent.roles.sport_specialist import (
    ROLE_ID as SS_ROLE_ID,
    validate_sport_specialist_output as validate_ss,
    valid_sport_specialist_payload as ss_payload,
    REGISTRY_ENTRY as SS_ENTRY,
)
from gate_engine.universal_agent.roles.failure_contradiction import (
    ROLE_ID as FC_ROLE_ID,
    validate_failure_contradiction_output as validate_fc,
    valid_failure_contradiction_payload as fc_payload,
    REGISTRY_ENTRY as FC_ENTRY,
)
from gate_engine.universal_agent.roles.final_refresh import (
    ROLE_ID as FR_ROLE_ID,
    validate_final_refresh_output as validate_fr,
    valid_final_refresh_payload as fr_payload,
    REGISTRY_ENTRY as FR_ENTRY,
)
from gate_engine.universal_agent.roles.registry_b1 import (
    ALL_B1_ENTRIES,
    build_b1_registry,
    register_b1_roles,
)


# ── Helpers ────────────────────────────────────────────────────────────────────

def _assert_valid(tc: unittest.TestCase, result: Any) -> None:
    tc.assertIs(result, OUTPUT_VALID, f"Expected OUTPUT_VALID, got {result!r}")


def _assert_violation(tc: unittest.TestCase, result: Any, expected_code: str) -> None:
    tc.assertIsNot(result, OUTPUT_VALID, "Expected a violation, got OUTPUT_VALID")
    tc.assertFalse(result, "Violation should be falsy")
    tc.assertEqual(
        result.code, expected_code,
        f"Expected code {expected_code!r}, got {result.code!r}: {result.message}",
    )


# ══════════════════════════════════════════════════════════════════════════════
# T1 — RoleBase
# ══════════════════════════════════════════════════════════════════════════════

class TestRoleBase(unittest.TestCase):
    """Shared role-base infrastructure tests."""

    def test_schema_version_is_string(self):
        self.assertIsInstance(SCHEMA_VERSION, str)
        self.assertTrue(SCHEMA_VERSION.strip())

    def test_role_violation_code_constants_exist(self):
        self.assertEqual(RoleViolationCode.ROLE_ID_MISMATCH, "ROLE_ID_MISMATCH")
        self.assertEqual(RoleViolationCode.INVALID_ENUM_VALUE, "INVALID_ENUM_VALUE")

    def test_evidence_availability_constants(self):
        self.assertEqual(EvidenceAvailability.AVAILABLE, "AVAILABLE")
        self.assertEqual(EvidenceAvailability.UNKNOWN,   "UNKNOWN")
        self.assertEqual(EvidenceAvailability.MISSING,   "MISSING")

    def test_six_distinct_role_ids(self):
        ids = [DSI_ROLE_ID, NS_ROLE_ID, MEL_ROLE_ID, SS_ROLE_ID, FC_ROLE_ID, FR_ROLE_ID]
        self.assertEqual(len(ids), len(set(ids)), "Role IDs must all be distinct")

    def test_all_role_ids_nonempty_strings(self):
        for rid in [DSI_ROLE_ID, NS_ROLE_ID, MEL_ROLE_ID, SS_ROLE_ID, FC_ROLE_ID, FR_ROLE_ID]:
            self.assertIsInstance(rid, str)
            self.assertTrue(rid.strip(), f"Role ID {rid!r} must not be blank")


# ══════════════════════════════════════════════════════════════════════════════
# Mixin: adversarial tests shared across all six roles
# ══════════════════════════════════════════════════════════════════════════════

class _RoleAdversarialMixin:
    """
    Common adversarial tests mixed into each role's test class.
    Subclasses must define: validate(), valid_payload(), and
    a dict-valued advisory_findings field name via _dict_field_for_nested_test.
    """

    # Override in each subclass to point at a dict-type advisory_findings field.
    # Used for the nested-governance-key smuggling test at depth >= 2.
    _dict_field_for_nested_test: str = ""

    def validate(self, payload: dict) -> Any:
        raise NotImplementedError

    def valid_payload(self) -> dict:
        raise NotImplementedError

    # ── Valid payloads ────────────────────────────────────────────────────────

    def test_valid_minimal_passes(self):
        _assert_valid(self, self.validate(self.valid_payload()))

    def test_output_valid_is_truthy(self):
        result = self.validate(self.valid_payload())
        self.assertTrue(result, "OUTPUT_VALID must be truthy")

    # ── Root-level governance smuggling ──────────────────────────────────────

    def test_governance_key_terminal_label_at_root(self):
        p = self.valid_payload()
        p["terminal_label"] = "WATCH"
        _assert_violation(self, self.validate(p), OutputViolationCode.FORBIDDEN_GOVERNANCE_KEY)

    def test_governance_key_can_execute_at_root(self):
        p = self.valid_payload()
        p["can_execute"] = False
        _assert_violation(self, self.validate(p), OutputViolationCode.FORBIDDEN_GOVERNANCE_KEY)

    def test_governance_key_final_decision_at_root(self):
        p = self.valid_payload()
        p["final_decision"] = "PLAY"
        _assert_violation(self, self.validate(p), OutputViolationCode.FORBIDDEN_GOVERNANCE_KEY)

    def test_governance_key_stake_tier_at_root(self):
        p = self.valid_payload()
        p["stake_tier"] = "STANDARD"
        _assert_violation(self, self.validate(p), OutputViolationCode.FORBIDDEN_GOVERNANCE_KEY)

    def test_governance_key_capital_at_root(self):
        p = self.valid_payload()
        p["capital"] = 100.0
        _assert_violation(self, self.validate(p), OutputViolationCode.FORBIDDEN_GOVERNANCE_KEY)

    # ── advisory_findings governance smuggling (Phase 1 catches these) ────────

    def test_governance_key_in_advisory_findings_depth1(self):
        p = self.valid_payload()
        p["advisory_findings"]["can_execute"] = False
        _assert_violation(self, self.validate(p), OutputViolationCode.FORBIDDEN_GOVERNANCE_KEY)

    def test_governance_key_final_decision_in_advisory_findings(self):
        p = self.valid_payload()
        p["advisory_findings"]["final_decision"] = "ABORT"
        _assert_violation(self, self.validate(p), OutputViolationCode.FORBIDDEN_GOVERNANCE_KEY)

    def test_governance_key_is_playable_in_advisory_findings(self):
        p = self.valid_payload()
        p["advisory_findings"]["is_playable"] = True
        _assert_violation(self, self.validate(p), OutputViolationCode.FORBIDDEN_GOVERNANCE_KEY)

    def test_governance_key_trade_in_advisory_findings(self):
        p = self.valid_payload()
        p["advisory_findings"]["trade"] = "BUY"
        _assert_violation(self, self.validate(p), OutputViolationCode.FORBIDDEN_GOVERNANCE_KEY)

    # ── advisory_findings nested governance smuggling (still Phase 1) ─────────

    def test_governance_key_nested_in_dict_field(self):
        """Governance key at depth ≥ 2 inside a dict-valued advisory field."""
        field = getattr(self, "_dict_field_for_nested_test", "")
        if not field:
            self.skipTest("_dict_field_for_nested_test not set for this role")
        p = self.valid_payload()
        p["advisory_findings"][field]["_injected_terminal_label"] = "FINAL"
        # Rename the injected key to a real forbidden key
        p["advisory_findings"][field]["terminal_label"] = "FINAL"
        del p["advisory_findings"][field]["_injected_terminal_label"]
        _assert_violation(self, self.validate(p), OutputViolationCode.FORBIDDEN_GOVERNANCE_KEY)

    def test_governance_key_nested_in_list_item(self):
        """Governance key smuggled inside a list item dict anywhere in findings."""
        p = self.valid_payload()
        # Use data_gaps_identified if available (is a list), else advisory_findings directly
        if "data_gaps_identified" in p["advisory_findings"]:
            p["advisory_findings"]["data_gaps_identified"] = [
                {"terminal_label": "HACKED"}
            ]
        elif "contradictions" in p["advisory_findings"]:
            p["advisory_findings"]["contradictions"] = [
                {"terminal_label": "HACKED"}
            ]
        else:
            # Inject into a list field via news_items or failures
            p["advisory_findings"]["news_items"] = [{"terminal_label": "HACKED"}]
        _assert_violation(self, self.validate(p), OutputViolationCode.FORBIDDEN_GOVERNANCE_KEY)

    # ── Root extra field ──────────────────────────────────────────────────────

    def test_extra_root_field_rejected(self):
        p = self.valid_payload()
        p["unexpected_root_field"] = "x"
        _assert_violation(self, self.validate(p), OutputViolationCode.EXTRA_FIELD)

    # ── advisory_only enforcement ─────────────────────────────────────────────

    def test_advisory_only_false_rejected(self):
        p = self.valid_payload()
        p["advisory_only"] = False
        _assert_violation(self, self.validate(p), OutputViolationCode.ADVISORY_ONLY_NOT_TRUE)

    def test_advisory_only_int_one_rejected(self):
        p = self.valid_payload()
        p["advisory_only"] = 1
        _assert_violation(self, self.validate(p), OutputViolationCode.ADVISORY_ONLY_NOT_TRUE)

    def test_advisory_only_string_true_rejected(self):
        p = self.valid_payload()
        p["advisory_only"] = "True"
        _assert_violation(self, self.validate(p), OutputViolationCode.ADVISORY_ONLY_NOT_TRUE)

    # ── advisory_findings role_id mismatch ────────────────────────────────────

    def test_wrong_role_id_rejected(self):
        p = self.valid_payload()
        p["advisory_findings"]["role_id"] = "WRONG_ROLE"
        _assert_violation(self, self.validate(p), RoleViolationCode.ROLE_ID_MISMATCH)

    def test_missing_role_id_rejected(self):
        p = self.valid_payload()
        del p["advisory_findings"]["role_id"]
        # Role ID missing → caught as MISSING_REQUIRED (role_id check fires first)
        result = self.validate(p)
        self.assertIsNot(result, OUTPUT_VALID)

    def test_missing_schema_version_rejected(self):
        p = self.valid_payload()
        del p["advisory_findings"]["schema_version"]
        _assert_violation(self, self.validate(p), OutputViolationCode.MISSING_REQUIRED_FIELD)

    # ── advisory_findings extra field ─────────────────────────────────────────

    def test_extra_advisory_findings_field_rejected(self):
        p = self.valid_payload()
        p["advisory_findings"]["completely_unknown_advisory_field"] = "x"
        _assert_violation(self, self.validate(p), OutputViolationCode.EXTRA_FIELD)

    # ── Non-dict payload ──────────────────────────────────────────────────────

    def test_none_payload_rejected(self):
        result = self.validate(None)
        self.assertIsNot(result, OUTPUT_VALID)

    def test_list_payload_rejected(self):
        result = self.validate(["not", "a", "dict"])
        self.assertIsNot(result, OUTPUT_VALID)


# ══════════════════════════════════════════════════════════════════════════════
# T2 — Data/Slate Integrity
# ══════════════════════════════════════════════════════════════════════════════

class TestDataSlateIntegrityRole(_RoleAdversarialMixin, unittest.TestCase):
    _dict_field_for_nested_test = "source_coverage"

    def validate(self, payload):
        return validate_dsi(payload)

    def valid_payload(self):
        return dsi_payload()

    def test_role_id_constant(self):
        self.assertEqual(DSI_ROLE_ID, "DATA_SLATE_INTEGRITY")

    def test_valid_full_with_all_optional_fields(self):
        p = dsi_payload(
            stale_sources=["bbref"],
            timestamp_audit={"primary": "2026-08-09T12:00:00+00:00"},
            integrity_confidence="HIGH",
        )
        _assert_valid(self, validate_dsi(p))

    def test_unknown_status_value_accepted(self):
        """UNKNOWN is a valid evidence state — not fabricated."""
        p = dsi_payload(data_freshness_status="UNKNOWN")
        _assert_valid(self, validate_dsi(p))

    def test_missing_status_value_accepted(self):
        """MISSING is a valid evidence state — preserves absence explicitly."""
        p = dsi_payload(data_freshness_status="MISSING")
        _assert_valid(self, validate_dsi(p))

    def test_invalid_freshness_enum_rejected(self):
        p = dsi_payload(data_freshness_status="FABRICATED")
        _assert_violation(self, validate_dsi(p), RoleViolationCode.INVALID_ENUM_VALUE)

    def test_invalid_consistency_enum_rejected(self):
        p = dsi_payload(slate_consistency_check="MAYBE")
        _assert_violation(self, validate_dsi(p), RoleViolationCode.INVALID_ENUM_VALUE)

    def test_invalid_confidence_enum_rejected(self):
        p = dsi_payload(integrity_confidence="VERY_HIGH")
        _assert_violation(self, validate_dsi(p), RoleViolationCode.INVALID_ENUM_VALUE)

    def test_source_coverage_wrong_type_rejected(self):
        p = dsi_payload(source_coverage="not-a-dict")
        _assert_violation(self, validate_dsi(p), OutputViolationCode.WRONG_TYPE)

    def test_data_gaps_identified_wrong_type_rejected(self):
        p = dsi_payload(data_gaps_identified="not-a-list")
        _assert_violation(self, validate_dsi(p), OutputViolationCode.WRONG_TYPE)

    def test_missing_data_freshness_status(self):
        p = dsi_payload()
        del p["advisory_findings"]["data_freshness_status"]
        _assert_violation(self, validate_dsi(p), OutputViolationCode.MISSING_REQUIRED_FIELD)

    def test_missing_slate_consistency_check(self):
        p = dsi_payload()
        del p["advisory_findings"]["slate_consistency_check"]
        _assert_violation(self, validate_dsi(p), OutputViolationCode.MISSING_REQUIRED_FIELD)

    def test_missing_source_coverage(self):
        p = dsi_payload()
        del p["advisory_findings"]["source_coverage"]
        _assert_violation(self, validate_dsi(p), OutputViolationCode.MISSING_REQUIRED_FIELD)

    def test_missing_data_gaps_identified(self):
        p = dsi_payload()
        del p["advisory_findings"]["data_gaps_identified"]
        _assert_violation(self, validate_dsi(p), OutputViolationCode.MISSING_REQUIRED_FIELD)

    def test_empty_data_gaps_list_valid(self):
        """Empty list for data_gaps_identified is valid (no gaps found)."""
        p = dsi_payload(data_gaps_identified=[])
        _assert_valid(self, validate_dsi(p))

    def test_registry_entry_agent_id(self):
        self.assertEqual(DSI_ENTRY.agent_id, "uac-data-slate-integrity-v1")

    def test_registry_entry_role_matches_role_id(self):
        self.assertEqual(DSI_ENTRY.role, DSI_ROLE_ID)

    def test_registry_entry_advisory_only_true(self):
        self.assertTrue(DSI_ENTRY.advisory_only)


# ══════════════════════════════════════════════════════════════════════════════
# T3 — News/Status
# ══════════════════════════════════════════════════════════════════════════════

class TestNewsStatusRole(_RoleAdversarialMixin, unittest.TestCase):
    _dict_field_for_nested_test = ""  # no top-level dict field; use list fallback

    def validate(self, payload):
        return validate_ns(payload)

    def valid_payload(self):
        return ns_payload()

    def test_role_id_constant(self):
        self.assertEqual(NS_ROLE_ID, "NEWS_STATUS")

    def test_valid_full_with_all_optional_fields(self):
        p = ns_payload(
            news_items=["Player listed as active, no injury designation."],
            status_confidence="HIGH",
            dnp_risk=False,
        )
        _assert_valid(self, validate_ns(p))

    def test_unknown_player_status_accepted(self):
        p = ns_payload(player_status="UNKNOWN")
        _assert_valid(self, validate_ns(p))

    def test_missing_player_status_accepted(self):
        """MISSING is a valid explicit evidence state."""
        p = ns_payload(player_status="MISSING")
        _assert_valid(self, validate_ns(p))

    def test_invalid_player_status_enum_rejected(self):
        p = ns_payload(player_status="INJURED")  # not a valid enum value
        _assert_violation(self, validate_ns(p), RoleViolationCode.INVALID_ENUM_VALUE)

    def test_invalid_confidence_enum_rejected(self):
        p = ns_payload(status_confidence="CERTAIN")
        _assert_violation(self, validate_ns(p), RoleViolationCode.INVALID_ENUM_VALUE)

    def test_injury_flag_wrong_type_rejected(self):
        p = ns_payload(injury_flag="yes")  # should be bool
        _assert_violation(self, validate_ns(p), OutputViolationCode.WRONG_TYPE)

    def test_injury_flag_int_rejected(self):
        p = ns_payload(injury_flag=1)
        _assert_violation(self, validate_ns(p), OutputViolationCode.WRONG_TYPE)

    def test_news_items_wrong_type_rejected(self):
        p = ns_payload(news_items="not a list")
        _assert_violation(self, validate_ns(p), OutputViolationCode.WRONG_TYPE)

    def test_missing_player_status_field(self):
        p = ns_payload()
        del p["advisory_findings"]["player_status"]
        _assert_violation(self, validate_ns(p), OutputViolationCode.MISSING_REQUIRED_FIELD)

    def test_missing_status_source_field(self):
        p = ns_payload()
        del p["advisory_findings"]["status_source"]
        _assert_violation(self, validate_ns(p), OutputViolationCode.MISSING_REQUIRED_FIELD)

    def test_missing_status_as_of_field(self):
        p = ns_payload()
        del p["advisory_findings"]["status_as_of"]
        _assert_violation(self, validate_ns(p), OutputViolationCode.MISSING_REQUIRED_FIELD)

    def test_missing_injury_flag_field(self):
        p = ns_payload()
        del p["advisory_findings"]["injury_flag"]
        _assert_violation(self, validate_ns(p), OutputViolationCode.MISSING_REQUIRED_FIELD)

    def test_status_as_of_unknown_string_accepted(self):
        """'UNKNOWN' is valid — timestamps may not be available."""
        p = ns_payload(status_as_of="UNKNOWN")
        _assert_valid(self, validate_ns(p))

    def test_dnp_risk_wrong_type_rejected(self):
        p = ns_payload(dnp_risk="maybe")
        _assert_violation(self, validate_ns(p), OutputViolationCode.WRONG_TYPE)

    def test_registry_entry_advisory_only_true(self):
        self.assertTrue(NS_ENTRY.advisory_only)

    def test_governance_key_in_list_item(self):
        """Governance key inside a news_items list element."""
        p = ns_payload(news_items=[{"terminal_label": "HACKED"}])
        _assert_violation(self, validate_ns(p), OutputViolationCode.FORBIDDEN_GOVERNANCE_KEY)


# ══════════════════════════════════════════════════════════════════════════════
# T4 — Market/Exact-Line
# ══════════════════════════════════════════════════════════════════════════════

class TestMarketExactLineRole(_RoleAdversarialMixin, unittest.TestCase):
    _dict_field_for_nested_test = ""  # no dict field; list fallback

    def validate(self, payload):
        return validate_mel(payload)

    def valid_payload(self):
        return mel_payload()

    def test_role_id_constant(self):
        self.assertEqual(MEL_ROLE_ID, "MARKET_EXACT_LINE")

    def test_valid_full_with_all_optional_fields(self):
        p = mel_payload(
            line_movement_note="Line moved from 24 to 24.5 overnight.",
            line_confidence="HIGH",
        )
        _assert_valid(self, validate_mel(p))

    def test_line_not_confirmed_with_none_values(self):
        """None for confirmed_line/odds is valid when market not found."""
        p = mel_payload(
            line_confirmed=False,
            line_source="UNKNOWN",
            market_status="UNKNOWN",
            confirmed_line=None,
            over_odds=None,
            under_odds=None,
        )
        _assert_valid(self, validate_mel(p))

    def test_invalid_market_status_enum_rejected(self):
        p = mel_payload(market_status="PAUSED")
        _assert_violation(self, validate_mel(p), RoleViolationCode.INVALID_ENUM_VALUE)

    def test_invalid_confidence_enum_rejected(self):
        p = mel_payload(line_confidence="VERY_HIGH")
        _assert_violation(self, validate_mel(p), RoleViolationCode.INVALID_ENUM_VALUE)

    def test_line_confirmed_wrong_type_rejected(self):
        p = mel_payload(line_confirmed="yes")
        _assert_violation(self, validate_mel(p), OutputViolationCode.WRONG_TYPE)

    def test_line_source_wrong_type_rejected(self):
        p = mel_payload(line_source=123)
        _assert_violation(self, validate_mel(p), OutputViolationCode.WRONG_TYPE)

    def test_missing_line_confirmed_field(self):
        p = mel_payload()
        del p["advisory_findings"]["line_confirmed"]
        _assert_violation(self, validate_mel(p), OutputViolationCode.MISSING_REQUIRED_FIELD)

    def test_missing_line_source_field(self):
        p = mel_payload()
        del p["advisory_findings"]["line_source"]
        _assert_violation(self, validate_mel(p), OutputViolationCode.MISSING_REQUIRED_FIELD)

    def test_missing_market_status_field(self):
        p = mel_payload()
        del p["advisory_findings"]["market_status"]
        _assert_violation(self, validate_mel(p), OutputViolationCode.MISSING_REQUIRED_FIELD)

    def test_market_unknown_is_valid_explicit_state(self):
        p = mel_payload(market_status="UNKNOWN")
        _assert_valid(self, validate_mel(p))

    def test_registry_entry_advisory_only_true(self):
        self.assertTrue(MEL_ENTRY.advisory_only)

    def test_governance_key_nested_in_findings(self):
        """Governance key nested arbitrarily inside advisory_findings."""
        p = mel_payload()
        # Inject at depth 1 in advisory_findings (list item with forbidden key)
        p["advisory_findings"]["line_movement_note_list"] = None
        # Remove that — just do a simple depth-1 injection
        del p["advisory_findings"]["line_movement_note_list"]
        p["advisory_findings"]["governance_state"] = "CLEAN"
        _assert_violation(self, validate_mel(p), OutputViolationCode.FORBIDDEN_GOVERNANCE_KEY)


# ══════════════════════════════════════════════════════════════════════════════
# T5 — Sport Specialist
# ══════════════════════════════════════════════════════════════════════════════

class TestSportSpecialistRole(_RoleAdversarialMixin, unittest.TestCase):
    _dict_field_for_nested_test = "statistical_assessment"

    def validate(self, payload):
        return validate_ss(payload)

    def valid_payload(self):
        return ss_payload()

    def test_role_id_constant(self):
        self.assertEqual(SS_ROLE_ID, "SPORT_SPECIALIST")

    def test_valid_full_with_all_optional_fields(self):
        p = ss_payload(
            missing_metrics=["vs_opponent_avg"],
            assessment_confidence="MEDIUM",
            model_inputs_used={"game_window": 10, "stat_key": "points"},
        )
        _assert_valid(self, validate_ss(p))

    def test_valid_wnba_sport(self):
        p = ss_payload(sport="WNBA")
        _assert_valid(self, validate_ss(p))

    def test_valid_tennis_sport(self):
        p = ss_payload(sport="TENNIS")
        _assert_valid(self, validate_ss(p))

    def test_empty_sport_string_rejected(self):
        p = ss_payload(sport="")
        result = validate_ss(p)
        self.assertIsNot(result, OUTPUT_VALID)

    def test_whitespace_sport_string_rejected(self):
        p = ss_payload(sport="   ")
        result = validate_ss(p)
        self.assertIsNot(result, OUTPUT_VALID)

    def test_invalid_confidence_enum_rejected(self):
        p = ss_payload(assessment_confidence="CERTAIN")
        _assert_violation(self, validate_ss(p), RoleViolationCode.INVALID_ENUM_VALUE)

    def test_statistical_assessment_wrong_type_rejected(self):
        p = ss_payload(statistical_assessment="not-a-dict")
        _assert_violation(self, validate_ss(p), OutputViolationCode.WRONG_TYPE)

    def test_key_metrics_wrong_type_rejected(self):
        p = ss_payload(key_metrics="not-a-list")
        _assert_violation(self, validate_ss(p), OutputViolationCode.WRONG_TYPE)

    def test_missing_sport_field(self):
        p = ss_payload()
        del p["advisory_findings"]["sport"]
        _assert_violation(self, validate_ss(p), OutputViolationCode.MISSING_REQUIRED_FIELD)

    def test_missing_statistical_assessment(self):
        p = ss_payload()
        del p["advisory_findings"]["statistical_assessment"]
        _assert_violation(self, validate_ss(p), OutputViolationCode.MISSING_REQUIRED_FIELD)

    def test_missing_key_metrics(self):
        p = ss_payload()
        del p["advisory_findings"]["key_metrics"]
        _assert_violation(self, validate_ss(p), OutputViolationCode.MISSING_REQUIRED_FIELD)

    def test_unknown_values_in_assessment_valid(self):
        """'UNKNOWN' values inside statistical_assessment are valid explicit states."""
        p = ss_payload(statistical_assessment={
            "recent_avg": "UNKNOWN",
            "season_avg": "MISSING",
        })
        _assert_valid(self, validate_ss(p))

    def test_governance_key_inside_statistical_assessment(self):
        """Governance key smuggled inside the statistical_assessment dict."""
        p = ss_payload()
        p["advisory_findings"]["statistical_assessment"]["terminal_label"] = "PLAY"
        _assert_violation(self, validate_ss(p), OutputViolationCode.FORBIDDEN_GOVERNANCE_KEY)

    def test_governance_key_inside_model_inputs_used(self):
        """Governance key two levels deep in an optional dict field."""
        p = ss_payload(model_inputs_used={"can_execute": False})
        _assert_violation(self, validate_ss(p), OutputViolationCode.FORBIDDEN_GOVERNANCE_KEY)

    def test_registry_entry_advisory_only_true(self):
        self.assertTrue(SS_ENTRY.advisory_only)


# ══════════════════════════════════════════════════════════════════════════════
# T6 — Failure/Contradiction
# ══════════════════════════════════════════════════════════════════════════════

class TestFailureContradictionRole(_RoleAdversarialMixin, unittest.TestCase):
    _dict_field_for_nested_test = ""

    def validate(self, payload):
        return validate_fc(payload)

    def valid_payload(self):
        return fc_payload()

    def test_role_id_constant(self):
        self.assertEqual(FC_ROLE_ID, "FAILURE_CONTRADICTION")

    def test_valid_full_with_contradictions_and_failures(self):
        p = fc_payload(
            contradiction_detected=True,
            failure_detected=True,
            resolution_recommendation="HOLD",
            contradiction_severity="MEDIUM",
            contradictions=[{"field": "line", "sources": ["a", "b"], "values": [24, 25]}],
            failures=[{"source": "espn", "reason": "HTTP 503"}],
        )
        _assert_valid(self, validate_fc(p))

    def test_resolution_unknown_valid(self):
        p = fc_payload(resolution_recommendation="UNKNOWN")
        _assert_valid(self, validate_fc(p))

    def test_invalid_resolution_enum_rejected(self):
        p = fc_payload(resolution_recommendation="MAYBE")
        _assert_violation(self, validate_fc(p), RoleViolationCode.INVALID_ENUM_VALUE)

    def test_invalid_severity_enum_rejected(self):
        p = fc_payload(contradiction_severity="CRITICAL")
        _assert_violation(self, validate_fc(p), RoleViolationCode.INVALID_ENUM_VALUE)

    def test_contradiction_detected_wrong_type_rejected(self):
        p = fc_payload(contradiction_detected="yes")
        _assert_violation(self, validate_fc(p), OutputViolationCode.WRONG_TYPE)

    def test_failure_detected_wrong_type_rejected(self):
        p = fc_payload(failure_detected=1)
        _assert_violation(self, validate_fc(p), OutputViolationCode.WRONG_TYPE)

    def test_contradictions_wrong_type_rejected(self):
        p = fc_payload(contradictions="not-a-list")
        _assert_violation(self, validate_fc(p), OutputViolationCode.WRONG_TYPE)

    def test_failures_wrong_type_rejected(self):
        p = fc_payload(failures={"key": "not-a-list"})
        _assert_violation(self, validate_fc(p), OutputViolationCode.WRONG_TYPE)

    def test_missing_contradiction_detected(self):
        p = fc_payload()
        del p["advisory_findings"]["contradiction_detected"]
        _assert_violation(self, validate_fc(p), OutputViolationCode.MISSING_REQUIRED_FIELD)

    def test_missing_failure_detected(self):
        p = fc_payload()
        del p["advisory_findings"]["failure_detected"]
        _assert_violation(self, validate_fc(p), OutputViolationCode.MISSING_REQUIRED_FIELD)

    def test_missing_resolution_recommendation(self):
        p = fc_payload()
        del p["advisory_findings"]["resolution_recommendation"]
        _assert_violation(self, validate_fc(p), OutputViolationCode.MISSING_REQUIRED_FIELD)

    def test_governance_key_inside_contradictions_list_item(self):
        """Governance key inside a contradiction descriptor dict (list item)."""
        p = fc_payload(
            contradiction_detected=True,
            contradictions=[{"capital": 500.0, "field": "line"}],
        )
        _assert_violation(self, validate_fc(p), OutputViolationCode.FORBIDDEN_GOVERNANCE_KEY)

    def test_governance_key_inside_failures_list_item(self):
        """Governance key inside a failure descriptor dict (list item)."""
        p = fc_payload(
            failure_detected=True,
            failures=[{"final_decision": "ABORT", "source": "espn"}],
        )
        _assert_violation(self, validate_fc(p), OutputViolationCode.FORBIDDEN_GOVERNANCE_KEY)

    def test_registry_entry_advisory_only_true(self):
        self.assertTrue(FC_ENTRY.advisory_only)

    def test_severity_none_is_valid_enum_value(self):
        """NONE severity means no contradiction at all — valid explicit state."""
        p = fc_payload(contradiction_severity="NONE")
        _assert_valid(self, validate_fc(p))


# ══════════════════════════════════════════════════════════════════════════════
# T7 — Final Refresh
# ══════════════════════════════════════════════════════════════════════════════

class TestFinalRefreshRole(_RoleAdversarialMixin, unittest.TestCase):
    _dict_field_for_nested_test = "role_outputs_summary"

    def validate(self, payload):
        return validate_fr(payload)

    def valid_payload(self):
        # Include role_outputs_summary so the mixin nested-dict injection test
        # has a dict field to inject into.
        return fr_payload(role_outputs_summary={"DATA_SLATE_INTEGRITY": "FRESH/CONSISTENT"})

    def test_role_id_constant(self):
        self.assertEqual(FR_ROLE_ID, "FINAL_REFRESH")

    def test_valid_full_with_all_optional_fields(self):
        p = fr_payload(
            synthesis_note="All roles completed; evidence valid as of 2026-08-09.",
            role_outputs_summary={
                "DATA_SLATE_INTEGRITY": "FRESH/CONSISTENT",
                "NEWS_STATUS": "ACTIVE, no injury",
            },
        )
        _assert_valid(self, validate_fr(p))

    def test_partial_refresh_valid(self):
        """PARTIAL refresh with some roles missing is a valid explicit state."""
        p = fr_payload(
            all_roles_completed=False,
            roles_completed=["DATA_SLATE_INTEGRITY", "NEWS_STATUS"],
            roles_missing=["MARKET_EXACT_LINE", "SPORT_SPECIALIST",
                           "FAILURE_CONTRADICTION"],
            refresh_status="PARTIAL",
            evidence_snapshot_valid=True,
        )
        _assert_valid(self, validate_fr(p))

    def test_failed_refresh_valid(self):
        p = fr_payload(
            all_roles_completed=False,
            roles_completed=[],
            roles_missing=["DATA_SLATE_INTEGRITY", "NEWS_STATUS",
                           "MARKET_EXACT_LINE", "SPORT_SPECIALIST",
                           "FAILURE_CONTRADICTION"],
            refresh_status="FAILED",
            evidence_snapshot_valid=False,
        )
        _assert_valid(self, validate_fr(p))

    def test_invalid_refresh_status_enum_rejected(self):
        p = fr_payload(refresh_status="ALMOST_DONE")
        _assert_violation(self, validate_fr(p), RoleViolationCode.INVALID_ENUM_VALUE)

    def test_all_roles_completed_wrong_type_rejected(self):
        p = fr_payload(all_roles_completed="yes")
        _assert_violation(self, validate_fr(p), OutputViolationCode.WRONG_TYPE)

    def test_roles_completed_wrong_type_rejected(self):
        p = fr_payload(roles_completed="not-a-list")
        _assert_violation(self, validate_fr(p), OutputViolationCode.WRONG_TYPE)

    def test_roles_missing_wrong_type_rejected(self):
        p = fr_payload(roles_missing={"key": "not-a-list"})
        _assert_violation(self, validate_fr(p), OutputViolationCode.WRONG_TYPE)

    def test_evidence_snapshot_valid_wrong_type_rejected(self):
        p = fr_payload(evidence_snapshot_valid="true")
        _assert_violation(self, validate_fr(p), OutputViolationCode.WRONG_TYPE)

    def test_synthesis_note_wrong_type_rejected(self):
        p = fr_payload(synthesis_note=["not", "a", "string"])
        _assert_violation(self, validate_fr(p), OutputViolationCode.WRONG_TYPE)

    def test_role_outputs_summary_wrong_type_rejected(self):
        p = fr_payload(role_outputs_summary="not-a-dict")
        _assert_violation(self, validate_fr(p), OutputViolationCode.WRONG_TYPE)

    def test_missing_all_roles_completed(self):
        p = fr_payload()
        del p["advisory_findings"]["all_roles_completed"]
        _assert_violation(self, validate_fr(p), OutputViolationCode.MISSING_REQUIRED_FIELD)

    def test_missing_roles_completed(self):
        p = fr_payload()
        del p["advisory_findings"]["roles_completed"]
        _assert_violation(self, validate_fr(p), OutputViolationCode.MISSING_REQUIRED_FIELD)

    def test_missing_roles_missing(self):
        p = fr_payload()
        del p["advisory_findings"]["roles_missing"]
        _assert_violation(self, validate_fr(p), OutputViolationCode.MISSING_REQUIRED_FIELD)

    def test_missing_refresh_status(self):
        p = fr_payload()
        del p["advisory_findings"]["refresh_status"]
        _assert_violation(self, validate_fr(p), OutputViolationCode.MISSING_REQUIRED_FIELD)

    def test_missing_evidence_snapshot_valid(self):
        p = fr_payload()
        del p["advisory_findings"]["evidence_snapshot_valid"]
        _assert_violation(self, validate_fr(p), OutputViolationCode.MISSING_REQUIRED_FIELD)

    def test_governance_key_inside_role_outputs_summary(self):
        """Governance key nested inside the optional summary dict."""
        p = fr_payload(role_outputs_summary={"DATA_SLATE_INTEGRITY": {"final_decision": "PLAY"}})
        _assert_violation(self, validate_fr(p), OutputViolationCode.FORBIDDEN_GOVERNANCE_KEY)

    def test_empty_roles_completed_and_missing_valid(self):
        """Empty lists are valid — no roles ran is an explicit state."""
        p = fr_payload(
            all_roles_completed=False,
            roles_completed=[],
            roles_missing=[],
            refresh_status="UNKNOWN",
            evidence_snapshot_valid=False,
        )
        _assert_valid(self, validate_fr(p))

    def test_registry_entry_advisory_only_true(self):
        self.assertTrue(FR_ENTRY.advisory_only)


# ══════════════════════════════════════════════════════════════════════════════
# T8 — B1 Registry (duplicate/unknown role handling, fail-closed)
# ══════════════════════════════════════════════════════════════════════════════

class TestB1Registry(unittest.TestCase):
    """Registry fail-closed behaviour for B1 roles."""

    def _fresh_registry(self) -> AgentRegistry:
        """Create a clean isolated registry for each test."""
        return build_b1_registry()

    def test_build_b1_registry_contains_exactly_six_entries(self):
        reg = self._fresh_registry()
        self.assertEqual(len(reg), 6)

    def test_all_six_role_ids_registered(self):
        reg = self._fresh_registry()
        for entry in ALL_B1_ENTRIES:
            self.assertTrue(
                reg.is_registered(entry.agent_id),
                f"agent_id '{entry.agent_id}' not found in registry",
            )

    def test_all_six_entries_in_all_b1_entries_tuple(self):
        self.assertEqual(len(ALL_B1_ENTRIES), 6)

    def test_all_entries_advisory_only_true(self):
        for entry in ALL_B1_ENTRIES:
            self.assertTrue(
                entry.advisory_only,
                f"Entry {entry.agent_id} must have advisory_only=True",
            )

    def test_advisory_only_cannot_be_set_false(self):
        """Structural guard: advisory_only setter raises AttributeError."""
        for entry in ALL_B1_ENTRIES:
            with self.assertRaises(AttributeError):
                entry.advisory_only = False

    def test_advisory_only_cannot_be_set_true(self):
        """Even setting it to True explicitly raises AttributeError (no setter)."""
        entry = DSI_ENTRY
        with self.assertRaises(AttributeError):
            entry.advisory_only = True

    def test_duplicate_registration_raises_key_error(self):
        """Registering the same agent_id twice raises KeyError (fail-closed)."""
        reg = AgentRegistry()
        reg.register(DSI_ENTRY)
        with self.assertRaises(KeyError):
            reg.register(DSI_ENTRY)

    def test_unknown_agent_id_raises_key_error(self):
        """Looking up an unregistered agent_id raises KeyError (fail-closed)."""
        reg = self._fresh_registry()
        with self.assertRaises(KeyError):
            reg.get("uac-nonexistent-role-v99")

    def test_is_registered_returns_false_for_unknown(self):
        reg = self._fresh_registry()
        self.assertFalse(reg.is_registered("made-up-agent-id"))

    def test_register_b1_roles_into_existing_registry(self):
        """register_b1_roles() populates any AgentRegistry instance."""
        reg = AgentRegistry()
        register_b1_roles(reg)
        self.assertEqual(len(reg), 6)

    def test_register_b1_roles_twice_raises(self):
        """Calling register_b1_roles twice on same registry raises KeyError."""
        reg = AgentRegistry()
        register_b1_roles(reg)
        with self.assertRaises(KeyError):
            register_b1_roles(reg)

    def test_get_dsi_entry_by_agent_id(self):
        reg = self._fresh_registry()
        entry = reg.get("uac-data-slate-integrity-v1")
        self.assertEqual(entry.role, DSI_ROLE_ID)

    def test_agents_for_lane_unknown_returns_all_six(self):
        """All B1 roles are registered with Lane.UNKNOWN."""
        from gate_engine.universal_agent.evidence_packet import Lane
        reg = self._fresh_registry()
        agents = reg.agents_for_lane(Lane.UNKNOWN)
        self.assertEqual(len(agents), 6)

    def test_all_entries_have_no_model_module(self):
        """B1 entries have no model wired (model wiring is B2+)."""
        for entry in ALL_B1_ENTRIES:
            self.assertIsNone(
                entry.model_module,
                f"Entry {entry.agent_id} should have model_module=None at B1",
            )

    def test_all_entries_have_non_empty_allowed_capabilities(self):
        for entry in ALL_B1_ENTRIES:
            self.assertTrue(
                len(entry.allowed_capabilities) > 0,
                f"Entry {entry.agent_id} must have at least one allowed capability",
            )

    def test_role_ids_match_entry_roles(self):
        expected = {
            "uac-data-slate-integrity-v1":   DSI_ROLE_ID,
            "uac-news-status-v1":            NS_ROLE_ID,
            "uac-market-exact-line-v1":      MEL_ROLE_ID,
            "uac-sport-specialist-v1":       SS_ROLE_ID,
            "uac-failure-contradiction-v1":  FC_ROLE_ID,
            "uac-final-refresh-v1":          FR_ROLE_ID,
        }
        for entry in ALL_B1_ENTRIES:
            self.assertEqual(
                entry.role, expected[entry.agent_id],
                f"Role mismatch for {entry.agent_id}",
            )


# ══════════════════════════════════════════════════════════════════════════════
# T9 — B0 Enforcement Sharing
# ══════════════════════════════════════════════════════════════════════════════

class TestB0EnforcementSharing(unittest.TestCase):
    """
    Proves that role validators invoke the shared B0 forbidden-key enforcement
    rather than reimplementing a duplicate scanner.

    Key assertions:
    1. _scan_forbidden_keys imported by role_base IS the same function object
       as _scan_forbidden_keys in output_contract (assertIs).
    2. validate_output_contract imported by role_base IS the same function object
       as validate_output_contract in output_contract (assertIs).
    3. Each role validator returns FORBIDDEN_GOVERNANCE_KEY for a governance key
       nested inside advisory_findings — which can only happen if B0's recursive
       scanner was invoked (role-specific Phase 2 does not scan for forbidden keys).
    """

    def test_scan_forbidden_keys_is_same_object_as_b0(self):
        """_scan_forbidden_keys in role_base == _scan_forbidden_keys in output_contract."""
        self.assertIs(
            _B1_SCAN_FORBIDDEN, _B0_SCAN_FORBIDDEN,
            "_scan_forbidden_keys in role_base must be the same function as in output_contract",
        )

    def test_validate_output_contract_is_same_object_as_b0(self):
        """validate_output_contract in role_base == validate_output_contract in output_contract."""
        self.assertIs(
            _B1_VALIDATE, _B0_VALIDATE,
            "validate_output_contract in role_base must be the same function as in output_contract",
        )

    def test_forbidden_keys_frozenset_shared_with_capability_boundary(self):
        """FORBIDDEN_GOVERNANCE_KEYS is shared between output_contract and capability_boundary."""
        from gate_engine.universal_agent.output_contract import FORBIDDEN_GOVERNANCE_KEYS as oc_fgk
        from gate_engine.universal_agent.capability_boundary import FORBIDDEN_GOVERNANCE_KEYS as cb_fgk
        self.assertIs(oc_fgk, cb_fgk, "FORBIDDEN_GOVERNANCE_KEYS must be the same object")

    def _all_validators(self):
        return [
            ("DATA_SLATE_INTEGRITY", validate_dsi, dsi_payload),
            ("NEWS_STATUS",          validate_ns,  ns_payload),
            ("MARKET_EXACT_LINE",    validate_mel, mel_payload),
            ("SPORT_SPECIALIST",     validate_ss,  ss_payload),
            ("FAILURE_CONTRADICTION", validate_fc, fc_payload),
            ("FINAL_REFRESH",        validate_fr,  fr_payload),
        ]

    def test_all_roles_catch_governance_key_at_root_via_b0(self):
        """All role validators reject a root-level governance key via B0 Phase 1."""
        for role_id, validator, builder in self._all_validators():
            with self.subTest(role=role_id):
                p = builder()
                p["terminal_label"] = "WATCH"
                result = validator(p)
                self.assertEqual(
                    result.code, OutputViolationCode.FORBIDDEN_GOVERNANCE_KEY,
                    f"Role {role_id}: expected FORBIDDEN_GOVERNANCE_KEY, got {result.code}",
                )

    def test_all_roles_catch_governance_key_in_advisory_findings_via_b0(self):
        """
        All role validators reject a governance key inside advisory_findings.
        This ONLY works if B0's recursive scanner (Phase 1) is invoked —
        role-specific Phase 2 does not scan for forbidden keys.
        """
        for role_id, validator, builder in self._all_validators():
            with self.subTest(role=role_id):
                p = builder()
                p["advisory_findings"]["can_execute"] = False
                result = validator(p)
                self.assertEqual(
                    result.code, OutputViolationCode.FORBIDDEN_GOVERNANCE_KEY,
                    f"Role {role_id}: governance key in advisory_findings not caught",
                )

    def test_sport_specialist_catches_governance_key_in_statistical_assessment(self):
        """
        Governance key at depth 2 (inside statistical_assessment dict) is caught
        by B0 Phase 1 recursive scanner — Phase 2 does not check this depth.
        """
        p = ss_payload()
        p["advisory_findings"]["statistical_assessment"]["final_decision"] = "PLAY"
        result = validate_ss(p)
        self.assertEqual(result.code, OutputViolationCode.FORBIDDEN_GOVERNANCE_KEY)

    def test_final_refresh_catches_governance_key_in_role_outputs_summary(self):
        """Governance key nested two levels deep in role_outputs_summary."""
        p = fr_payload(role_outputs_summary={
            "SOME_ROLE": {"stake_tier": "STANDARD"},
        })
        result = validate_fr(p)
        self.assertEqual(result.code, OutputViolationCode.FORBIDDEN_GOVERNANCE_KEY)

    def test_failure_contradiction_catches_governance_key_in_list_item(self):
        """Governance key inside a list item dict (contradictions list)."""
        p = fc_payload(
            contradiction_detected=True,
            contradictions=[{"capital": 100.0}],
        )
        result = validate_fc(p)
        self.assertEqual(result.code, OutputViolationCode.FORBIDDEN_GOVERNANCE_KEY)

    def test_all_roles_valid_payload_passes(self):
        """Sanity: all role builders produce passing payloads."""
        for role_id, validator, builder in self._all_validators():
            with self.subTest(role=role_id):
                result = validator(builder())
                self.assertIs(
                    result, OUTPUT_VALID,
                    f"Role {role_id}: valid_payload() should pass validation",
                )

    def test_b0_scan_is_called_for_advisory_findings_content(self):
        """
        Verify via mock that validate_output_contract is called during each
        role validator invocation. This confirms B0 Phase 1 is not bypassed.
        """
        from unittest.mock import patch, call
        for role_id, validator, builder in self._all_validators():
            with self.subTest(role=role_id):
                p = builder()
                with patch(
                    "gate_engine.universal_agent.roles.role_base.validate_output_contract",
                    wraps=_B0_VALIDATE,
                ) as mock_b0:
                    validator(p)
                    mock_b0.assert_called_once_with(p)


if __name__ == "__main__":
    unittest.main()
