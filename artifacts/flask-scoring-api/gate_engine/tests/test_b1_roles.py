"""
gate_engine/tests/test_b1_roles.py
WOW-PATCH-2026-08-09-UNIVERSAL-AGENT-CORE-V1 / Phase B1 acceptance tests.

Covers:
  (a) Each of the six role contracts validates required fields and rejects
      missing/wrong-type inputs.
  (b) registry_b1 contains exactly six entries.
  (c) All six entries carry advisory_only=True.
  (d) No role contract permits a terminal label or can_execute/capital
      field in its output schema.
  (e) Authority constants in roles/__init__.py.

No network or DB calls anywhere.
"""
from __future__ import annotations

import unittest

import gate_engine.universal_agent.roles as roles_pkg
from gate_engine.universal_agent.agent_registry import AgentRegistry
from gate_engine.universal_agent.output_contract import (
    FORBIDDEN_GOVERNANCE_KEYS,
    OUTPUT_VALID,
    OutputViolationCode,
)
from gate_engine.universal_agent.roles.role_base import (
    RoleViolationCode,
    SCHEMA_VERSION,
)
from gate_engine.universal_agent.roles import (
    data_slate_integrity as dsi,
    failure_contradiction as fc,
    final_refresh as fr,
    market_exact_line as mel,
    news_status as ns,
    sport_specialist as ss,
)
from gate_engine.universal_agent.roles.registry_b1 import (
    ALL_B1_ENTRIES,
    build_b1_registry,
    register_b1_roles,
)

# (module, validator, payload_helper, role_id, one required field,
#  one enum field + bad value, one typed field + bad value)
ROLE_CASES = [
    (dsi, dsi.validate_data_slate_integrity_output,
     dsi.valid_data_slate_integrity_payload, "DATA_SLATE_INTEGRITY",
     "data_freshness_status",
     ("data_freshness_status", "SORT_OF_FRESH"),
     ("source_coverage", "not-a-dict")),
    (ns, ns.validate_news_status_output,
     ns.valid_news_status_payload, "NEWS_STATUS",
     "player_status",
     ("player_status", "PLAYING_MAYBE"),
     ("injury_flag", "yes")),
    (mel, mel.validate_market_exact_line_output,
     mel.valid_market_exact_line_payload, "MARKET_EXACT_LINE",
     "line_confirmed",
     ("market_status", "HALF_OPEN"),
     ("line_confirmed", "true")),
    (ss, ss.validate_sport_specialist_output,
     ss.valid_sport_specialist_payload, "SPORT_SPECIALIST",
     "sport",
     ("assessment_confidence", "SUPREME"),
     ("key_metrics", "not-a-list")),
    (fc, fc.validate_failure_contradiction_output,
     fc.valid_failure_contradiction_payload, "FAILURE_CONTRADICTION",
     "resolution_recommendation",
     ("resolution_recommendation", "SHRUG"),
     ("contradiction_detected", "nope")),
    (fr, fr.validate_final_refresh_output,
     fr.valid_final_refresh_payload, "FINAL_REFRESH",
     "refresh_status",
     ("refresh_status", "ALMOST"),
     ("roles_completed", "not-a-list")),
]


# ── (a) Per-role contract validation ──────────────────────────────────────────

class TestRoleContracts(unittest.TestCase):
    def test_valid_payload_passes_for_each_role(self):
        for mod, validator, helper, role_id, *_ in ROLE_CASES:
            self.assertIs(validator(helper()), OUTPUT_VALID, role_id)

    def test_missing_required_field_rejected(self):
        for mod, validator, helper, role_id, req_field, _, _t in ROLE_CASES:
            payload = helper()
            del payload["advisory_findings"][req_field]
            result = validator(payload)
            self.assertEqual(
                result.code, OutputViolationCode.MISSING_REQUIRED_FIELD,
                f"{role_id}.{req_field}",
            )

    def test_missing_schema_version_rejected(self):
        for mod, validator, helper, role_id, *_ in ROLE_CASES:
            payload = helper()
            del payload["advisory_findings"]["schema_version"]
            result = validator(payload)
            self.assertEqual(
                result.code, OutputViolationCode.MISSING_REQUIRED_FIELD, role_id
            )

    def test_invalid_enum_value_rejected(self):
        for mod, validator, helper, role_id, _req, enum_case, _t in ROLE_CASES:
            field, bad = enum_case
            payload = helper(**{field: bad})
            result = validator(payload)
            self.assertEqual(
                result.code, RoleViolationCode.INVALID_ENUM_VALUE,
                f"{role_id}.{field}",
            )

    def test_wrong_type_rejected(self):
        for mod, validator, helper, role_id, _req, _e, type_case in ROLE_CASES:
            field, bad = type_case
            payload = helper(**{field: bad})
            result = validator(payload)
            self.assertEqual(
                result.code, OutputViolationCode.WRONG_TYPE,
                f"{role_id}.{field}",
            )

    def test_role_id_mismatch_rejected(self):
        for mod, validator, helper, role_id, *_ in ROLE_CASES:
            payload = helper(role_id="SOME_OTHER_ROLE")
            result = validator(payload)
            self.assertEqual(result.code, RoleViolationCode.ROLE_ID_MISMATCH, role_id)

    def test_extra_findings_field_rejected(self):
        for mod, validator, helper, role_id, *_ in ROLE_CASES:
            payload = helper(surprise_field="x")
            result = validator(payload)
            self.assertEqual(result.code, OutputViolationCode.EXTRA_FIELD, role_id)

    def test_forbidden_governance_key_in_findings_caught_by_phase1(self):
        for mod, validator, helper, role_id, *_ in ROLE_CASES:
            payload = helper()
            payload["advisory_findings"]["nested"] = {"can_execute": True}
            result = validator(payload)
            self.assertEqual(
                result.code, OutputViolationCode.FORBIDDEN_GOVERNANCE_KEY, role_id
            )

    def test_non_dict_payload_rejected(self):
        for mod, validator, helper, role_id, *_ in ROLE_CASES:
            result = validator("not a dict")
            self.assertEqual(result.code, OutputViolationCode.NOT_A_DICT, role_id)

    def test_advisory_only_not_true_rejected(self):
        for mod, validator, helper, role_id, *_ in ROLE_CASES:
            payload = helper()
            payload["advisory_only"] = False
            result = validator(payload)
            self.assertEqual(
                result.code, OutputViolationCode.ADVISORY_ONLY_NOT_TRUE, role_id
            )

    def test_schema_version_constant(self):
        self.assertEqual(SCHEMA_VERSION, "1.0")
        for mod, _v, helper, role_id, *_ in ROLE_CASES:
            self.assertEqual(
                helper()["advisory_findings"]["schema_version"], SCHEMA_VERSION,
                role_id,
            )


# ── (b) + (c) registry_b1 ─────────────────────────────────────────────────────

class TestRegistryB1(unittest.TestCase):
    def test_exactly_six_entries(self):
        self.assertEqual(len(ALL_B1_ENTRIES), 6)

    def test_build_b1_registry_contains_six(self):
        self.assertEqual(len(build_b1_registry()), 6)

    def test_all_entries_advisory_only_true(self):
        for entry in ALL_B1_ENTRIES:
            self.assertIs(entry.advisory_only, True, entry.agent_id)

    def test_agent_ids_unique(self):
        ids = [e.agent_id for e in ALL_B1_ENTRIES]
        self.assertEqual(len(ids), len(set(ids)))

    def test_role_ids_match_the_six_roles(self):
        expected = {
            "DATA_SLATE_INTEGRITY", "NEWS_STATUS", "MARKET_EXACT_LINE",
            "SPORT_SPECIALIST", "FAILURE_CONTRADICTION", "FINAL_REFRESH",
        }
        self.assertEqual({e.role for e in ALL_B1_ENTRIES}, expected)

    def test_duplicate_registration_fails_closed(self):
        registry = build_b1_registry()
        with self.assertRaises(KeyError):
            register_b1_roles(registry)

    def test_register_b1_roles_into_fresh_registry(self):
        registry = AgentRegistry()
        register_b1_roles(registry)
        self.assertEqual(len(registry), 6)

    def test_unknown_agent_lookup_fails_closed(self):
        with self.assertRaises(KeyError):
            build_b1_registry().get("uac-nonexistent-v1")

    def test_entries_have_nonempty_capabilities(self):
        for entry in ALL_B1_ENTRIES:
            self.assertTrue(entry.allowed_capabilities, entry.agent_id)


# ── (d) No governance authority in any role schema ─────────────────────────────

class TestNoAuthorityInRoleSchemas(unittest.TestCase):
    def test_no_forbidden_key_in_any_allowed_schema(self):
        for mod, _v, _h, role_id, *_ in ROLE_CASES:
            allowed = mod._ADVISORY_ALLOWED | mod._ADVISORY_REQUIRED
            for field in allowed:
                self.assertNotIn(
                    field.lower(), FORBIDDEN_GOVERNANCE_KEYS,
                    f"{role_id} schema permits forbidden field {field}",
                )

    def test_terminal_label_rejected_in_findings_for_each_role(self):
        for mod, validator, helper, role_id, *_ in ROLE_CASES:
            payload = helper(terminal_label="FINAL_APPROVED")
            result = validator(payload)
            self.assertEqual(
                result.code, OutputViolationCode.FORBIDDEN_GOVERNANCE_KEY, role_id
            )

    def test_can_execute_rejected_in_findings_for_each_role(self):
        for mod, validator, helper, role_id, *_ in ROLE_CASES:
            payload = helper(can_execute=True)
            result = validator(payload)
            self.assertEqual(
                result.code, OutputViolationCode.FORBIDDEN_GOVERNANCE_KEY, role_id
            )

    def test_capital_allocation_rejected_in_findings_for_each_role(self):
        for mod, validator, helper, role_id, *_ in ROLE_CASES:
            payload = helper(capital_allocation=100.0)
            result = validator(payload)
            self.assertEqual(
                result.code, OutputViolationCode.FORBIDDEN_GOVERNANCE_KEY, role_id
            )

    def test_stake_tier_rejected_in_findings_for_each_role(self):
        for mod, validator, helper, role_id, *_ in ROLE_CASES:
            payload = helper(stake_tier="A")
            result = validator(payload)
            self.assertEqual(
                result.code, OutputViolationCode.FORBIDDEN_GOVERNANCE_KEY, role_id
            )

    def test_role_base_reuses_b0_scan_no_reimplementation(self):
        from gate_engine.universal_agent import output_contract, roles
        self.assertIs(
            roles.role_base._scan_forbidden_keys,
            output_contract._scan_forbidden_keys,
        )


# ── (e) Authority constants in roles/__init__.py ──────────────────────────────

class TestB1AuthorityConstants(unittest.TestCase):
    def test_constants(self):
        self.assertIs(roles_pkg.can_execute, False)
        self.assertIs(roles_pkg.PRODUCTION_AUTHORITY, False)
        self.assertIs(roles_pkg.USER_OUTPUT_AUTHORITY, False)
        self.assertIs(roles_pkg.CAPITAL_AUTHORITY, False)
        self.assertIs(roles_pkg.NO_AUTO_PROMOTION, True)


if __name__ == "__main__":
    unittest.main()
