"""
tests/test_universal_agent_b2.py
WOW-PATCH-2026-08-09-UNIVERSAL-AGENT-CORE-V1 / Phase B2

Focused non-database B2 orchestration tests.

Coverage required by scope:
  P  All six roles see the same immutable packet identity (same Python id())
  F  Invalid / governance-contaminated role outputs fail closed (never ACCEPTED)
  M  Missing / failed roles cannot be silently treated as success
  A  Canonical bundle assembly is deterministic
  C  Contradiction recording is deterministic
  O  OrchestratorResult structure and invariants

Test classes
------------
  TestB2PacketIdentity       (P)  — same packet object reaches every runner
  TestB2FailClosed           (F)  — governance reject / invalid / runner fail / no runner
  TestB2MissingRolesBundled  (M)  — missing roles surface in failed/missing_role_ids
  TestB2BundleAssembly       (A)  — deterministic assembly, status transitions
  TestB2ContradictionRules   (C)  — all four rules + determinism
  TestB2OrchestratorResult   (O)  — result structure, invariants, type errors

No database is used in this file.  DB tests live in test_universal_agent_b2_db.py.
No app.py import, no live API call, no Weather code.
"""
from __future__ import annotations

import unittest

from gate_engine.universal_agent.bundle_assembler import (
    BundleStatus,
    assemble_bundle,
)
from gate_engine.universal_agent.contradiction_detector import (
    ContradictionRecord,
    detect_contradictions,
)
from gate_engine.universal_agent.evidence_packet import Lane, build_test_packet
from gate_engine.universal_agent.orchestrator import (
    B1_ROLE_IDS,
    OrchestratorResult,
    run_orchestrator,
)
from gate_engine.universal_agent.role_result import RoleResult
from gate_engine.universal_agent.role_runner import MockRoleRunner, RoleRunnerStatus
from gate_engine.universal_agent.roles.registry_b1 import build_b1_registry
from gate_engine.universal_agent.roles.data_slate_integrity import (
    valid_data_slate_integrity_payload,
)
from gate_engine.universal_agent.roles.news_status import (
    valid_news_status_payload,
)
from gate_engine.universal_agent.roles.market_exact_line import (
    valid_market_exact_line_payload,
)
from gate_engine.universal_agent.roles.sport_specialist import (
    valid_sport_specialist_payload,
)
from gate_engine.universal_agent.roles.failure_contradiction import (
    valid_failure_contradiction_payload,
)
from gate_engine.universal_agent.roles.final_refresh import (
    valid_final_refresh_payload,
)

# ── Shared helpers ─────────────────────────────────────────────────────────────

_ALL_AGENT_IDS = [
    "uac-data-slate-integrity-v1",
    "uac-news-status-v1",
    "uac-market-exact-line-v1",
    "uac-sport-specialist-v1",
    "uac-failure-contradiction-v1",
    "uac-final-refresh-v1",
]

_ALL_ROLE_IDS = list(B1_ROLE_IDS)

# Minimal valid presets — one clean payload per agent, all pass B0+B1 validation.
_VALID_PRESETS: dict = {
    "uac-data-slate-integrity-v1": valid_data_slate_integrity_payload(),
    "uac-news-status-v1":          valid_news_status_payload(),
    "uac-market-exact-line-v1":    valid_market_exact_line_payload(),
    "uac-sport-specialist-v1":     valid_sport_specialist_payload(),
    "uac-failure-contradiction-v1": valid_failure_contradiction_payload(),
    "uac-final-refresh-v1":        valid_final_refresh_payload(),
}


def _fresh_packet(**kw):
    return build_test_packet(**kw)


def _fresh_registry():
    return build_b1_registry()


def _run_all_valid(packet=None, registry=None, presets=None):
    """Run orchestrator with all-valid presets; returns (result, runner)."""
    if packet is None:
        packet = _fresh_packet()
    if registry is None:
        registry = _fresh_registry()
    if presets is None:
        presets = dict(_VALID_PRESETS)
    runner = MockRoleRunner(presets=presets)
    runners = {aid: runner for aid in _ALL_AGENT_IDS}
    result = run_orchestrator(packet, registry, runners, db_conn=None)
    return result, runner


# ══════════════════════════════════════════════════════════════════════════════
# P — Packet identity
# ══════════════════════════════════════════════════════════════════════════════

class TestB2PacketIdentity(unittest.TestCase):
    """
    All six role runners must receive the SAME EvidencePacket Python object
    (same id()), not a copy or a re-built instance.  Immutability of the
    frozen dataclass means no runner can mutate it, but identity proves
    the orchestrator fans the original reference, not a copy.
    """

    def test_all_six_roles_see_same_packet_object(self):
        packet = _fresh_packet()
        _, runner = _run_all_valid(packet=packet)
        ids = runner.packet_ids_seen()
        self.assertEqual(len(ids), 6)
        self.assertEqual(len(set(ids)), 1,
            f"Expected 1 unique packet id, got {len(set(ids))}: {ids}")

    def test_packet_id_matches_original(self):
        packet = _fresh_packet()
        _, runner = _run_all_valid(packet=packet)
        for seen_id in runner.packet_ids_seen():
            self.assertEqual(seen_id, id(packet))

    def test_all_six_snapshot_ids_match_packet(self):
        packet = _fresh_packet()
        _, runner = _run_all_valid(packet=packet)
        for snap_id in runner.snapshot_ids_seen():
            self.assertEqual(snap_id, packet.snapshot_id)

    def test_six_runner_calls_made(self):
        _, runner = _run_all_valid()
        self.assertEqual(len(runner.call_log), 6)

    def test_packet_is_frozen_cannot_be_mutated(self):
        packet = _fresh_packet()
        with self.assertRaises((AttributeError, TypeError)):
            packet.run_id = "mutated"  # type: ignore[misc]


# ══════════════════════════════════════════════════════════════════════════════
# F — Fail-closed for invalid / contaminated / missing outputs
# ══════════════════════════════════════════════════════════════════════════════

class TestB2FailClosed(unittest.TestCase):
    """
    Invalid or governance-contaminated runner outputs must never reach ACCEPTED
    status and must never appear in bundle.accepted_role_ids.
    """

    def _run_with_one_bad(self, agent_id: str, bad_payload) -> OrchestratorResult:
        presets = dict(_VALID_PRESETS)
        presets[agent_id] = bad_payload
        result, _ = _run_all_valid(presets=presets)
        return result

    # ── Governance contamination ───────────────────────────────────────────────

    def test_governance_key_at_root_is_governance_rejected(self):
        bad = {"can_execute": True, "advisory_only": True,
               "agent_id": "x", "lane": "PLAYER_PROPS",
               "snapshot_id": "s", "run_id": "r", "advisory_findings": {}}
        result = self._run_with_one_bad("uac-data-slate-integrity-v1", bad)
        r = result.result_for_agent("uac-data-slate-integrity-v1")
        self.assertEqual(r.status, RoleRunnerStatus.GOVERNANCE_REJECTED)

    def test_governance_key_in_advisory_findings_is_governance_rejected(self):
        bad = dict(_VALID_PRESETS["uac-news-status-v1"])
        bad = dict(bad)
        bad["advisory_findings"] = dict(bad["advisory_findings"])
        bad["advisory_findings"]["final_decision"] = "APPROVED"
        result = self._run_with_one_bad("uac-news-status-v1", bad)
        r = result.result_for_agent("uac-news-status-v1")
        self.assertEqual(r.status, RoleRunnerStatus.GOVERNANCE_REJECTED)

    def test_governance_rejected_not_in_accepted_role_ids(self):
        bad = {"can_execute": True, "advisory_only": True,
               "agent_id": "x", "lane": "PLAYER_PROPS",
               "snapshot_id": "s", "run_id": "r", "advisory_findings": {}}
        result = self._run_with_one_bad("uac-market-exact-line-v1", bad)
        self.assertNotIn("MARKET_EXACT_LINE", result.bundle.accepted_role_ids)

    def test_governance_rejected_in_failed_role_ids(self):
        bad = {"stake_tier": "HIGH", "advisory_only": True,
               "agent_id": "x", "lane": "PLAYER_PROPS",
               "snapshot_id": "s", "run_id": "r", "advisory_findings": {}}
        result = self._run_with_one_bad("uac-sport-specialist-v1", bad)
        self.assertIn("SPORT_SPECIALIST", result.bundle.failed_role_ids)

    # ── Invalid B1 schema ─────────────────────────────────────────────────────

    def test_wrong_role_id_in_findings_is_invalid(self):
        bad = valid_data_slate_integrity_payload(role_id="WRONG_ROLE")
        result = self._run_with_one_bad("uac-data-slate-integrity-v1", bad)
        r = result.result_for_agent("uac-data-slate-integrity-v1")
        self.assertEqual(r.status, RoleRunnerStatus.INVALID)

    def test_missing_required_finding_field_is_invalid(self):
        # Remove a required field from advisory_findings
        payload = dict(valid_news_status_payload())
        findings = dict(payload["advisory_findings"])
        del findings["player_status"]
        payload["advisory_findings"] = findings
        result = self._run_with_one_bad("uac-news-status-v1", payload)
        r = result.result_for_agent("uac-news-status-v1")
        self.assertEqual(r.status, RoleRunnerStatus.INVALID)

    def test_invalid_not_in_accepted_role_ids(self):
        bad = valid_data_slate_integrity_payload(role_id="WRONG_ROLE")
        result = self._run_with_one_bad("uac-data-slate-integrity-v1", bad)
        self.assertNotIn("DATA_SLATE_INTEGRITY", result.bundle.accepted_role_ids)

    def test_advisory_only_false_is_governance_rejected(self):
        bad = dict(valid_final_refresh_payload())
        bad["advisory_only"] = False
        result = self._run_with_one_bad("uac-final-refresh-v1", bad)
        r = result.result_for_agent("uac-final-refresh-v1")
        self.assertNotEqual(r.status, RoleRunnerStatus.ACCEPTED)

    # ── Runner failures ───────────────────────────────────────────────────────

    def test_runner_exception_is_runner_failed(self):
        presets = dict(_VALID_PRESETS)
        presets["uac-sport-specialist-v1"] = RuntimeError("simulated failure")
        result, _ = _run_all_valid(presets=presets)
        r = result.result_for_agent("uac-sport-specialist-v1")
        self.assertEqual(r.status, RoleRunnerStatus.RUNNER_FAILED)

    def test_runner_exception_error_message_preserved(self):
        presets = dict(_VALID_PRESETS)
        presets["uac-final-refresh-v1"] = ValueError("test-error-msg")
        result, _ = _run_all_valid(presets=presets)
        r = result.result_for_agent("uac-final-refresh-v1")
        self.assertIn("test-error-msg", r.error_message)

    def test_runner_returns_string_is_runner_failed(self):
        presets = dict(_VALID_PRESETS)
        presets["uac-failure-contradiction-v1"] = "not a dict"
        # MockRoleRunner will return the string — but orchestrator expects a dict
        # Override __call__ by injecting a callable that returns a string
        class StringRunner:
            def __call__(self, entry, packet):
                return "not a dict"
        registry = _fresh_registry()
        runners = {aid: MockRoleRunner(presets={aid: _VALID_PRESETS[aid]})
                   for aid in _ALL_AGENT_IDS}
        runners["uac-failure-contradiction-v1"] = StringRunner()
        packet = _fresh_packet()
        result = run_orchestrator(packet, registry, runners, db_conn=None)
        r = result.result_for_agent("uac-failure-contradiction-v1")
        self.assertEqual(r.status, RoleRunnerStatus.RUNNER_FAILED)

    def test_runner_failed_not_in_accepted_role_ids(self):
        presets = dict(_VALID_PRESETS)
        presets["uac-failure-contradiction-v1"] = RuntimeError("crash")
        result, _ = _run_all_valid(presets=presets)
        self.assertNotIn("FAILURE_CONTRADICTION", result.bundle.accepted_role_ids)

    # ── Missing runner ────────────────────────────────────────────────────────

    def test_no_runner_registered_is_no_runner_status(self):
        registry = _fresh_registry()
        runners = {aid: MockRoleRunner(presets={aid: _VALID_PRESETS[aid]})
                   for aid in _ALL_AGENT_IDS if aid != "uac-news-status-v1"}
        packet = _fresh_packet()
        result = run_orchestrator(packet, registry, runners, db_conn=None)
        r = result.result_for_agent("uac-news-status-v1")
        self.assertEqual(r.status, RoleRunnerStatus.NO_RUNNER)

    def test_no_runner_role_not_in_accepted_role_ids(self):
        registry = _fresh_registry()
        runners = {aid: MockRoleRunner(presets={aid: _VALID_PRESETS[aid]})
                   for aid in _ALL_AGENT_IDS if aid != "uac-news-status-v1"}
        packet = _fresh_packet()
        result = run_orchestrator(packet, registry, runners, db_conn=None)
        self.assertNotIn("NEWS_STATUS", result.bundle.accepted_role_ids)

    def test_no_runner_role_in_failed_role_ids(self):
        registry = _fresh_registry()
        runners = {aid: MockRoleRunner(presets={aid: _VALID_PRESETS[aid]})
                   for aid in _ALL_AGENT_IDS if aid != "uac-news-status-v1"}
        packet = _fresh_packet()
        result = run_orchestrator(packet, registry, runners, db_conn=None)
        self.assertIn("NEWS_STATUS", result.bundle.failed_role_ids)

    def test_empty_runner_dict_all_no_runner(self):
        registry = _fresh_registry()
        packet = _fresh_packet()
        result = run_orchestrator(packet, registry, {}, db_conn=None)
        for r in result.role_results:
            self.assertEqual(r.status, RoleRunnerStatus.NO_RUNNER)


# ══════════════════════════════════════════════════════════════════════════════
# M — Missing roles reflected in bundle
# ══════════════════════════════════════════════════════════════════════════════

class TestB2MissingRolesBundled(unittest.TestCase):
    """
    Failed / missing roles must surface explicitly in the bundle — never silently
    treated as success or omitted from the summary.
    """

    def test_all_failed_bundle_status_is_failed(self):
        registry = _fresh_registry()
        packet = _fresh_packet()
        result = run_orchestrator(packet, registry, {}, db_conn=None)
        self.assertEqual(result.bundle.bundle_status, BundleStatus.FAILED)

    def test_one_failed_bundle_status_is_partial(self):
        presets = dict(_VALID_PRESETS)
        presets["uac-final-refresh-v1"] = RuntimeError("crash")
        result, _ = _run_all_valid(presets=presets)
        self.assertEqual(result.bundle.bundle_status, BundleStatus.PARTIAL)

    def test_all_accepted_bundle_status_is_complete(self):
        result, _ = _run_all_valid()
        self.assertEqual(result.bundle.bundle_status, BundleStatus.COMPLETE)

    def test_failed_roles_not_in_accepted_role_ids(self):
        presets = dict(_VALID_PRESETS)
        presets["uac-failure-contradiction-v1"] = RuntimeError("crash")
        result, _ = _run_all_valid(presets=presets)
        self.assertNotIn("FAILURE_CONTRADICTION", result.bundle.accepted_role_ids)

    def test_failed_roles_in_failed_role_ids(self):
        presets = dict(_VALID_PRESETS)
        presets["uac-failure-contradiction-v1"] = RuntimeError("crash")
        result, _ = _run_all_valid(presets=presets)
        self.assertIn("FAILURE_CONTRADICTION", result.bundle.failed_role_ids)

    def test_six_failed_roles_all_in_failed_role_ids(self):
        registry = _fresh_registry()
        packet = _fresh_packet()
        result = run_orchestrator(packet, registry, {}, db_conn=None)
        self.assertEqual(len(result.bundle.failed_role_ids), 6)

    def test_partial_accepted_and_failed_counts_sum_to_six(self):
        presets = dict(_VALID_PRESETS)
        presets["uac-data-slate-integrity-v1"] = RuntimeError("crash")
        presets["uac-news-status-v1"] = RuntimeError("crash")
        result, _ = _run_all_valid(presets=presets)
        total = len(result.bundle.accepted_role_ids) + len(result.bundle.failed_role_ids)
        self.assertEqual(total, 6)


# ══════════════════════════════════════════════════════════════════════════════
# A — Bundle assembly determinism
# ══════════════════════════════════════════════════════════════════════════════

class TestB2BundleAssembly(unittest.TestCase):
    """
    assemble_bundle() is a pure function — same inputs produce the same output
    (modulo assembled_at timestamp, which can be injected for testing).
    """

    def test_accepted_findings_present_in_bundle(self):
        result, _ = _run_all_valid()
        self.assertIn("DATA_SLATE_INTEGRITY", result.bundle.accepted_findings)
        self.assertIn("NEWS_STATUS",          result.bundle.accepted_findings)
        self.assertIn("MARKET_EXACT_LINE",    result.bundle.accepted_findings)
        self.assertIn("SPORT_SPECIALIST",     result.bundle.accepted_findings)
        self.assertIn("FAILURE_CONTRADICTION",result.bundle.accepted_findings)
        self.assertIn("FINAL_REFRESH",        result.bundle.accepted_findings)

    def test_failed_role_has_no_findings_in_bundle(self):
        presets = dict(_VALID_PRESETS)
        presets["uac-news-status-v1"] = RuntimeError("crash")
        result, _ = _run_all_valid(presets=presets)
        self.assertNotIn("NEWS_STATUS", result.bundle.accepted_findings)

    def test_accepted_role_ids_is_sorted_tuple(self):
        result, _ = _run_all_valid()
        ids = result.bundle.accepted_role_ids
        self.assertIsInstance(ids, tuple)
        self.assertEqual(list(ids), sorted(ids))

    def test_bundle_run_id_matches_packet(self):
        packet = _fresh_packet(run_id="deterministic-run-99")
        result, _ = _run_all_valid(packet=packet)
        self.assertEqual(result.bundle.run_id, "deterministic-run-99")

    def test_bundle_snapshot_id_matches_packet(self):
        packet = _fresh_packet(snapshot_id="snap-fixed-001")
        result, _ = _run_all_valid(packet=packet)
        self.assertEqual(result.bundle.snapshot_id, "snap-fixed-001")

    def test_source_provenance_from_packet(self):
        packet = _fresh_packet(source_provenance={"primary": "test-url"})
        result, _ = _run_all_valid(packet=packet)
        self.assertEqual(result.bundle.source_provenance, {"primary": "test-url"})

    def test_source_failures_from_packet_in_bundle(self):
        packet = _fresh_packet(source_failures=[{"source": "ESPN", "reason": "timeout"}])
        result, _ = _run_all_valid(packet=packet)
        self.assertIn({"source": "ESPN", "reason": "timeout"}, result.bundle.source_failures)

    def test_assemble_bundle_with_fixed_assembled_at_is_deterministic(self):
        """Two calls with fixed assembled_at and identical inputs produce equal bundles."""
        packet = _fresh_packet()
        registry = _fresh_registry()
        presets = dict(_VALID_PRESETS)
        fixed_ts = "2026-08-09T12:00:00+00:00"

        runner1 = MockRoleRunner(presets=presets)
        result1 = run_orchestrator(packet, registry,
                                   {aid: runner1 for aid in _ALL_AGENT_IDS},
                                   db_conn=None)
        runner2 = MockRoleRunner(presets=presets)
        result2 = run_orchestrator(packet, registry,
                                   {aid: runner2 for aid in _ALL_AGENT_IDS},
                                   db_conn=None)

        # Fields that must be identical (excluding assembled_at + completed_at)
        self.assertEqual(result1.bundle.accepted_role_ids, result2.bundle.accepted_role_ids)
        self.assertEqual(result1.bundle.failed_role_ids,   result2.bundle.failed_role_ids)
        self.assertEqual(result1.bundle.bundle_status,     result2.bundle.bundle_status)
        self.assertEqual(result1.bundle.accepted_findings, result2.bundle.accepted_findings)

    def test_bundle_is_frozen_dataclass(self):
        result, _ = _run_all_valid()
        with self.assertRaises((AttributeError, TypeError)):
            result.bundle.bundle_status = "MUTATED"  # type: ignore[misc]

    def test_data_gaps_from_role_output_merged_into_bundle(self):
        """If a role output includes data_gaps, they appear in the bundle."""
        presets = dict(_VALID_PRESETS)
        payload_with_gaps = dict(valid_sport_specialist_payload())
        payload_with_gaps["data_gaps"] = [{"metric": "season_avg", "reason": "unavailable"}]
        presets["uac-sport-specialist-v1"] = payload_with_gaps
        result, _ = _run_all_valid(presets=presets)
        self.assertTrue(
            any("season_avg" in str(g) for g in result.bundle.data_gaps),
            "Expected season_avg gap in bundle.data_gaps"
        )

    def test_high_contradiction_downgrades_complete_to_partial(self):
        """COMPLETE → PARTIAL when a HIGH-severity contradiction is present."""
        # All roles ACCEPTED, but introduce a HIGH contradiction via Rule 3
        presets = dict(_VALID_PRESETS)
        presets["uac-failure-contradiction-v1"] = valid_failure_contradiction_payload(
            contradiction_detected=True,
            contradiction_severity="HIGH",
        )
        result, _ = _run_all_valid(presets=presets)
        self.assertEqual(result.bundle.bundle_status, BundleStatus.PARTIAL,
            "HIGH contradiction should downgrade COMPLETE to PARTIAL")


# ══════════════════════════════════════════════════════════════════════════════
# C — Contradiction detection
# ══════════════════════════════════════════════════════════════════════════════

class TestB2ContradictionRules(unittest.TestCase):
    """
    detect_contradictions() is a pure function.  Same inputs → same output.
    Rules fire only on ACCEPTED roles; failed roles are invisible to rules.
    """

    # ── Rule 1: PLAYER_OUT_POSITIVE_ASSESSMENT ────────────────────────────────

    def test_rule1_fires_player_out_with_assessment(self):
        presets = dict(_VALID_PRESETS)
        # NEWS_STATUS: player OUT; SPORT_SPECIALIST: assessment present (dict ≠ exclusion strs)
        presets["uac-news-status-v1"] = valid_news_status_payload(player_status="OUT")
        result, _ = _run_all_valid(presets=presets)
        rule_ids = [c.rule_id for c in result.contradictions]
        self.assertIn("RULE-1-PLAYER-OUT-POSITIVE-ASSESSMENT", rule_ids)

    def test_rule1_does_not_fire_player_active(self):
        presets = dict(_VALID_PRESETS)
        presets["uac-news-status-v1"] = valid_news_status_payload(player_status="ACTIVE")
        result, _ = _run_all_valid(presets=presets)
        rule_ids = [c.rule_id for c in result.contradictions]
        self.assertNotIn("RULE-1-PLAYER-OUT-POSITIVE-ASSESSMENT", rule_ids)

    def test_rule1_severity_is_high(self):
        presets = dict(_VALID_PRESETS)
        presets["uac-news-status-v1"] = valid_news_status_payload(player_status="OUT")
        result, _ = _run_all_valid(presets=presets)
        for c in result.contradictions:
            if c.rule_id == "RULE-1-PLAYER-OUT-POSITIVE-ASSESSMENT":
                self.assertEqual(c.severity, "HIGH")
                return
        self.fail("Rule 1 contradiction not found")

    def test_rule1_does_not_fire_when_news_status_failed(self):
        """If NEWS_STATUS is not ACCEPTED, Rule 1 should not fire."""
        presets = dict(_VALID_PRESETS)
        presets["uac-news-status-v1"] = RuntimeError("crash")
        result, _ = _run_all_valid(presets=presets)
        rule_ids = [c.rule_id for c in result.contradictions]
        self.assertNotIn("RULE-1-PLAYER-OUT-POSITIVE-ASSESSMENT", rule_ids)

    # ── Rule 2: STALE_DATA_LINE_CONFIRMED ────────────────────────────────────

    def test_rule2_fires_stale_data_line_confirmed(self):
        presets = dict(_VALID_PRESETS)
        presets["uac-data-slate-integrity-v1"] = valid_data_slate_integrity_payload(
            data_freshness_status="STALE"
        )
        # MARKET_EXACT_LINE default: line_confirmed=True → Rule 2 fires
        result, _ = _run_all_valid(presets=presets)
        rule_ids = [c.rule_id for c in result.contradictions]
        self.assertIn("RULE-2-STALE-DATA-LINE-CONFIRMED", rule_ids)

    def test_rule2_does_not_fire_fresh_data(self):
        presets = dict(_VALID_PRESETS)
        presets["uac-data-slate-integrity-v1"] = valid_data_slate_integrity_payload(
            data_freshness_status="FRESH"
        )
        result, _ = _run_all_valid(presets=presets)
        rule_ids = [c.rule_id for c in result.contradictions]
        self.assertNotIn("RULE-2-STALE-DATA-LINE-CONFIRMED", rule_ids)

    def test_rule2_does_not_fire_stale_but_not_confirmed(self):
        presets = dict(_VALID_PRESETS)
        presets["uac-data-slate-integrity-v1"] = valid_data_slate_integrity_payload(
            data_freshness_status="STALE"
        )
        presets["uac-market-exact-line-v1"] = valid_market_exact_line_payload(
            line_confirmed=False
        )
        result, _ = _run_all_valid(presets=presets)
        rule_ids = [c.rule_id for c in result.contradictions]
        self.assertNotIn("RULE-2-STALE-DATA-LINE-CONFIRMED", rule_ids)

    # ── Rule 3: FAILURE_HIGH_SEVERITY ─────────────────────────────────────────

    def test_rule3_fires_contradiction_detected_high(self):
        presets = dict(_VALID_PRESETS)
        presets["uac-failure-contradiction-v1"] = valid_failure_contradiction_payload(
            contradiction_detected=True,
            contradiction_severity="HIGH",
        )
        result, _ = _run_all_valid(presets=presets)
        rule_ids = [c.rule_id for c in result.contradictions]
        self.assertIn("RULE-3-FAILURE-HIGH-SEVERITY", rule_ids)

    def test_rule3_does_not_fire_medium_severity(self):
        presets = dict(_VALID_PRESETS)
        presets["uac-failure-contradiction-v1"] = valid_failure_contradiction_payload(
            contradiction_detected=True,
            contradiction_severity="MEDIUM",
        )
        result, _ = _run_all_valid(presets=presets)
        rule_ids = [c.rule_id for c in result.contradictions]
        self.assertNotIn("RULE-3-FAILURE-HIGH-SEVERITY", rule_ids)

    def test_rule3_does_not_fire_detected_false(self):
        presets = dict(_VALID_PRESETS)
        presets["uac-failure-contradiction-v1"] = valid_failure_contradiction_payload(
            contradiction_detected=False,
            contradiction_severity="HIGH",
        )
        result, _ = _run_all_valid(presets=presets)
        rule_ids = [c.rule_id for c in result.contradictions]
        self.assertNotIn("RULE-3-FAILURE-HIGH-SEVERITY", rule_ids)

    # ── Rule 4: FINAL_REFRESH_COMPLETE_WITH_MISSING ───────────────────────────

    def test_rule4_fires_when_final_refresh_claims_complete_but_roles_failed(self):
        presets = dict(_VALID_PRESETS)
        # FINAL_REFRESH claims all_roles_completed=True (already the default)
        # One role is crashed → orchestrator's non_accepted_ids is non-empty
        presets["uac-data-slate-integrity-v1"] = RuntimeError("crash")
        result, _ = _run_all_valid(presets=presets)
        rule_ids = [c.rule_id for c in result.contradictions]
        self.assertIn("RULE-4-FINAL-REFRESH-COMPLETE-WITH-MISSING-ROLES", rule_ids)

    def test_rule4_does_not_fire_all_accepted(self):
        result, _ = _run_all_valid()
        rule_ids = [c.rule_id for c in result.contradictions]
        self.assertNotIn("RULE-4-FINAL-REFRESH-COMPLETE-WITH-MISSING-ROLES", rule_ids)

    # ── Determinism ───────────────────────────────────────────────────────────

    def test_contradiction_detection_deterministic(self):
        """Two identical runs produce identical contradiction tuples."""
        presets = dict(_VALID_PRESETS)
        presets["uac-news-status-v1"] = valid_news_status_payload(player_status="OUT")
        presets["uac-data-slate-integrity-v1"] = valid_data_slate_integrity_payload(
            data_freshness_status="STALE"
        )
        result1, _ = _run_all_valid(presets=presets, packet=_fresh_packet(
            run_id="det-run-1", snapshot_id="det-snap-1"))
        result2, _ = _run_all_valid(presets=presets, packet=_fresh_packet(
            run_id="det-run-2", snapshot_id="det-snap-2"))
        r1_ids = [c.rule_id for c in result1.contradictions]
        r2_ids = [c.rule_id for c in result2.contradictions]
        self.assertEqual(r1_ids, r2_ids)

    def test_contradictions_sorted_by_rule_id(self):
        presets = dict(_VALID_PRESETS)
        presets["uac-news-status-v1"] = valid_news_status_payload(player_status="OUT")
        presets["uac-data-slate-integrity-v1"] = valid_data_slate_integrity_payload(
            data_freshness_status="STALE"
        )
        result, _ = _run_all_valid(presets=presets)
        rule_ids = [c.rule_id for c in result.contradictions]
        self.assertEqual(rule_ids, sorted(rule_ids))

    def test_no_contradictions_clean_data(self):
        """Standard valid presets with no edge cases produce zero contradictions."""
        result, _ = _run_all_valid()
        self.assertEqual(len(result.contradictions), 0)

    def test_detect_contradictions_pure_function_direct(self):
        """Call detect_contradictions() directly — verify pure function behaviour."""
        from gate_engine.universal_agent.role_runner import RoleRunnerStatus
        # Build two minimal RoleResult objects
        ns_result = RoleResult(
            agent_id="uac-news-status-v1",
            role_id="NEWS_STATUS",
            status=RoleRunnerStatus.ACCEPTED,
            raw_output={},
            advisory_findings={"player_status": "OUT"},
            violation_code=None,
            violation_message=None,
            latency_ms=None,
            error_message=None,
        )
        ss_result = RoleResult(
            agent_id="uac-sport-specialist-v1",
            role_id="SPORT_SPECIALIST",
            status=RoleRunnerStatus.ACCEPTED,
            raw_output={},
            advisory_findings={"statistical_assessment": {"recent_avg": 22.0}},
            violation_code=None,
            violation_message=None,
            latency_ms=None,
            error_message=None,
        )
        results_by_role = {"NEWS_STATUS": ns_result, "SPORT_SPECIALIST": ss_result}
        contradictions = detect_contradictions(results_by_role)
        rule_ids = [c.rule_id for c in contradictions]
        self.assertIn("RULE-1-PLAYER-OUT-POSITIVE-ASSESSMENT", rule_ids)
        # Second call produces identical output (pure)
        contradictions2 = detect_contradictions(results_by_role)
        self.assertEqual(contradictions, contradictions2)


# ══════════════════════════════════════════════════════════════════════════════
# O — OrchestratorResult structure and invariants
# ══════════════════════════════════════════════════════════════════════════════

class TestB2OrchestratorResult(unittest.TestCase):

    def test_result_is_frozen_dataclass(self):
        result, _ = _run_all_valid()
        with self.assertRaises((AttributeError, TypeError)):
            result.persisted = True  # type: ignore[misc]

    def test_run_id_matches_packet(self):
        packet = _fresh_packet(run_id="result-test-run")
        result, _ = _run_all_valid(packet=packet)
        self.assertEqual(result.run_id, "result-test-run")

    def test_snapshot_id_matches_packet(self):
        packet = _fresh_packet(snapshot_id="result-test-snap")
        result, _ = _run_all_valid(packet=packet)
        self.assertEqual(result.snapshot_id, "result-test-snap")

    def test_no_db_conn_persisted_is_false(self):
        result, _ = _run_all_valid()
        self.assertFalse(result.persisted)

    def test_role_results_is_tuple(self):
        result, _ = _run_all_valid()
        self.assertIsInstance(result.role_results, tuple)

    def test_role_results_has_six_entries(self):
        result, _ = _run_all_valid()
        self.assertEqual(len(result.role_results), 6)

    def test_contradictions_is_tuple(self):
        result, _ = _run_all_valid()
        self.assertIsInstance(result.contradictions, tuple)

    def test_non_evidencepacket_raises_type_error(self):
        registry = _fresh_registry()
        with self.assertRaises(TypeError):
            run_orchestrator({"not": "a packet"}, registry, {}, db_conn=None)

    def test_b1_role_ids_constant_has_six_entries(self):
        self.assertEqual(len(B1_ROLE_IDS), 6)

    def test_b1_role_ids_are_unique(self):
        self.assertEqual(len(set(B1_ROLE_IDS)), 6)

    def test_accepted_count_helper(self):
        result, _ = _run_all_valid()
        self.assertEqual(result.accepted_count(), 6)

    def test_failed_count_helper_with_one_fail(self):
        presets = dict(_VALID_PRESETS)
        presets["uac-news-status-v1"] = RuntimeError("crash")
        result, _ = _run_all_valid(presets=presets)
        self.assertEqual(result.failed_count(), 1)

    def test_result_for_role_returns_correct_entry(self):
        result, _ = _run_all_valid()
        r = result.result_for_role("DATA_SLATE_INTEGRITY")
        self.assertIsNotNone(r)
        self.assertEqual(r.role_id, "DATA_SLATE_INTEGRITY")

    def test_result_for_agent_returns_correct_entry(self):
        result, _ = _run_all_valid()
        r = result.result_for_agent("uac-final-refresh-v1")
        self.assertIsNotNone(r)
        self.assertEqual(r.agent_id, "uac-final-refresh-v1")

    def test_result_for_unknown_role_returns_none(self):
        result, _ = _run_all_valid()
        self.assertIsNone(result.result_for_role("NONEXISTENT_ROLE"))


if __name__ == "__main__":
    unittest.main()
