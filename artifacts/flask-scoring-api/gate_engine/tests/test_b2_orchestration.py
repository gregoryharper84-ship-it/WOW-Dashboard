"""
gate_engine/tests/test_b2_orchestration.py
WOW-PATCH-2026-08-09-UNIVERSAL-AGENT-CORE-V1 / Phase B2 acceptance tests.

Covers:
  (a) RoleResult is a frozen dataclass.
  (b) contradiction_detector fires all four deterministic rules.
  (c) bundle_assembler produces a frozen EvidenceBundle; COMPLETE only when
      all roles pass with no HIGH contradiction.
  (d) run_orchestrator returns an OrchestratorResult that grants no execution
      authority (no can_execute field; no forbidden governance keys anywhere).
  (e) MockRoleRunner SKIPPED_RESUMED is treated as effectively accepted.
  (f) Authority constants correct.

No network or DB calls anywhere (db_conn=None throughout).
"""
from __future__ import annotations

import dataclasses
import unittest

import gate_engine.universal_agent as ua_pkg
from gate_engine.universal_agent.bundle_assembler import (
    BundleStatus,
    EvidenceBundle,
    assemble_bundle,
)
from gate_engine.universal_agent.contradiction_detector import (
    ContradictionRecord,
    detect_contradictions,
)
from gate_engine.universal_agent.evidence_packet import build_test_packet
from gate_engine.universal_agent.orchestrator import (
    B1_ROLE_IDS,
    OrchestratorResult,
    run_orchestrator,
)
from gate_engine.universal_agent.output_contract import _scan_forbidden_keys
from gate_engine.universal_agent.role_result import RoleResult
from gate_engine.universal_agent.role_runner import (
    MockRoleRunner,
    RoleRunnerStatus,
)
from gate_engine.universal_agent.roles import (
    valid_data_slate_integrity_payload,
    valid_failure_contradiction_payload,
    valid_final_refresh_payload,
    valid_market_exact_line_payload,
    valid_news_status_payload,
    valid_sport_specialist_payload,
)
from gate_engine.universal_agent.roles.registry_b1 import (
    ALL_B1_ENTRIES,
    build_b1_registry,
)

_PAYLOAD_BY_ROLE = {
    "DATA_SLATE_INTEGRITY":  valid_data_slate_integrity_payload,
    "NEWS_STATUS":           valid_news_status_payload,
    "MARKET_EXACT_LINE":     valid_market_exact_line_payload,
    "SPORT_SPECIALIST":      valid_sport_specialist_payload,
    "FAILURE_CONTRADICTION": valid_failure_contradiction_payload,
    "FINAL_REFRESH":         valid_final_refresh_payload,
}


def _make_result(role_id: str, status: str = RoleRunnerStatus.ACCEPTED,
                 findings: dict | None = None,
                 raw_output: dict | None = None) -> RoleResult:
    if findings is None and status == RoleRunnerStatus.ACCEPTED:
        findings = _PAYLOAD_BY_ROLE[role_id]()["advisory_findings"]
    return RoleResult(
        agent_id=f"uac-{role_id.lower().replace('_', '-')}-v1",
        role_id=role_id,
        status=status,
        raw_output=raw_output,
        advisory_findings=findings if status == RoleRunnerStatus.ACCEPTED else None,
        violation_code=None,
        violation_message=None,
        latency_ms=1,
        error_message=None,
    )


def _all_accepted_results(**findings_overrides) -> list[RoleResult]:
    out = []
    for rid in B1_ROLE_IDS:
        findings = _PAYLOAD_BY_ROLE[rid]()["advisory_findings"]
        findings.update(findings_overrides.get(rid, {}))
        out.append(_make_result(rid, findings=findings))
    return out


def _full_mock_runners(overrides: dict | None = None) -> dict:
    """dict[agent_id → MockRoleRunner] with a valid payload preset per role."""
    presets = {}
    for entry in ALL_B1_ENTRIES:
        payload = _PAYLOAD_BY_ROLE[entry.role]()
        if overrides and entry.role in overrides:
            payload = overrides[entry.role]
        presets[entry.agent_id] = payload
    runner = MockRoleRunner(presets)
    return {entry.agent_id: runner for entry in ALL_B1_ENTRIES}, runner


# ── (a) RoleResult frozen dataclass ────────────────────────────────────────────

class TestRoleResult(unittest.TestCase):
    def test_is_frozen_dataclass(self):
        self.assertTrue(dataclasses.is_dataclass(RoleResult))
        self.assertTrue(RoleResult.__dataclass_params__.frozen)

    def test_mutation_raises(self):
        r = _make_result("NEWS_STATUS")
        with self.assertRaises(dataclasses.FrozenInstanceError):
            r.status = "MUTATED"

    def test_accepted_property(self):
        self.assertTrue(_make_result("NEWS_STATUS").accepted)
        self.assertFalse(
            _make_result("NEWS_STATUS", RoleRunnerStatus.RUNNER_FAILED).accepted
        )

    def test_skipped_resumed_effectively_accepted_but_not_accepted(self):
        r = _make_result("NEWS_STATUS", RoleRunnerStatus.SKIPPED_RESUMED)
        self.assertFalse(r.accepted)
        self.assertTrue(r.effectively_accepted)
        self.assertFalse(r.failed)

    def test_failed_property_for_all_failure_statuses(self):
        for status in (RoleRunnerStatus.INVALID, RoleRunnerStatus.RUNNER_FAILED,
                       RoleRunnerStatus.GOVERNANCE_REJECTED,
                       RoleRunnerStatus.NO_RUNNER,
                       RoleRunnerStatus.BOUNDARY_BLOCKED):
            self.assertTrue(_make_result("NEWS_STATUS", status).failed, status)

    def test_to_dict_has_no_raw_output(self):
        d = _make_result("NEWS_STATUS").to_dict()
        self.assertNotIn("raw_output", d)
        self.assertEqual(d["role_id"], "NEWS_STATUS")


# ── (b) Contradiction detector rules ───────────────────────────────────────────

class TestContradictionDetector(unittest.TestCase):
    def _by_role(self, results):
        return {r.role_id: r for r in results}

    def test_clean_results_produce_no_contradictions(self):
        results = self._by_role(_all_accepted_results())
        self.assertEqual(detect_contradictions(results), ())

    def test_rule1_player_out_positive_assessment_fires(self):
        results = self._by_role(_all_accepted_results(
            NEWS_STATUS={"player_status": "OUT"},
        ))
        records = detect_contradictions(results)
        rule_ids = [c.rule_id for c in records]
        self.assertIn("RULE-1-PLAYER-OUT-POSITIVE-ASSESSMENT", rule_ids)
        rec = records[rule_ids.index("RULE-1-PLAYER-OUT-POSITIVE-ASSESSMENT")]
        self.assertEqual(rec.severity, "HIGH")
        self.assertEqual(rec.roles_involved, ("NEWS_STATUS", "SPORT_SPECIALIST"))

    def test_rule1_not_fired_when_player_active(self):
        results = self._by_role(_all_accepted_results())
        self.assertNotIn(
            "RULE-1-PLAYER-OUT-POSITIVE-ASSESSMENT",
            [c.rule_id for c in detect_contradictions(results)],
        )

    def test_rule1_not_fired_when_assessment_unknown(self):
        results = self._by_role(_all_accepted_results(
            NEWS_STATUS={"player_status": "OUT"},
            SPORT_SPECIALIST={"statistical_assessment": "UNKNOWN"},
        ))
        self.assertNotIn(
            "RULE-1-PLAYER-OUT-POSITIVE-ASSESSMENT",
            [c.rule_id for c in detect_contradictions(results)],
        )

    def test_rule2_stale_line_confirmed_fires(self):
        results = self._by_role(_all_accepted_results(
            DATA_SLATE_INTEGRITY={"data_freshness_status": "STALE"},
            MARKET_EXACT_LINE={"line_confirmed": True},
        ))
        records = detect_contradictions(results)
        rec = next(c for c in records
                   if c.rule_id == "RULE-2-STALE-DATA-LINE-CONFIRMED")
        self.assertEqual(rec.severity, "MEDIUM")

    def test_rule2_not_fired_when_line_not_confirmed(self):
        results = self._by_role(_all_accepted_results(
            DATA_SLATE_INTEGRITY={"data_freshness_status": "STALE"},
            MARKET_EXACT_LINE={"line_confirmed": False},
        ))
        self.assertNotIn(
            "RULE-2-STALE-DATA-LINE-CONFIRMED",
            [c.rule_id for c in detect_contradictions(results)],
        )

    def test_rule3_high_severity_contradiction_fires(self):
        results = self._by_role(_all_accepted_results(
            FAILURE_CONTRADICTION={
                "contradiction_detected": True,
                "contradiction_severity": "HIGH",
            },
        ))
        records = detect_contradictions(results)
        rec = next(c for c in records
                   if c.rule_id == "RULE-3-FAILURE-HIGH-SEVERITY")
        self.assertEqual(rec.severity, "HIGH")

    def test_rule3_not_fired_on_medium_severity(self):
        results = self._by_role(_all_accepted_results(
            FAILURE_CONTRADICTION={
                "contradiction_detected": True,
                "contradiction_severity": "MEDIUM",
            },
        ))
        self.assertNotIn(
            "RULE-3-FAILURE-HIGH-SEVERITY",
            [c.rule_id for c in detect_contradictions(results)],
        )

    def test_rule4_complete_claim_with_missing_roles_fires(self):
        results = self._by_role(_all_accepted_results())
        records = detect_contradictions(results, missing_role_ids=["NEWS_STATUS"])
        rec = next(
            c for c in records
            if c.rule_id == "RULE-4-FINAL-REFRESH-COMPLETE-WITH-MISSING-ROLES"
        )
        self.assertEqual(rec.severity, "MEDIUM")

    def test_rule4_not_fired_without_missing_roles(self):
        results = self._by_role(_all_accepted_results())
        self.assertEqual(detect_contradictions(results, missing_role_ids=[]), ())

    def test_only_accepted_roles_contribute(self):
        results = self._by_role(_all_accepted_results(
            NEWS_STATUS={"player_status": "OUT"},
        ))
        # Demote NEWS_STATUS to failed — rule 1 must not fire.
        results["NEWS_STATUS"] = _make_result(
            "NEWS_STATUS", RoleRunnerStatus.RUNNER_FAILED
        )
        self.assertNotIn(
            "RULE-1-PLAYER-OUT-POSITIVE-ASSESSMENT",
            [c.rule_id for c in detect_contradictions(results)],
        )

    def test_deterministic_sorted_output(self):
        results = self._by_role(_all_accepted_results(
            NEWS_STATUS={"player_status": "OUT"},
            DATA_SLATE_INTEGRITY={"data_freshness_status": "STALE"},
            MARKET_EXACT_LINE={"line_confirmed": True},
            FAILURE_CONTRADICTION={
                "contradiction_detected": True,
                "contradiction_severity": "HIGH",
            },
        ))
        first  = detect_contradictions(results, missing_role_ids=["X"])
        second = detect_contradictions(results, missing_role_ids=["X"])
        self.assertEqual(first, second)
        self.assertEqual(len(first), 4)
        self.assertEqual([c.rule_id for c in first],
                         sorted(c.rule_id for c in first))

    def test_contradiction_record_frozen(self):
        rec = ContradictionRecord(
            rule_id="R", description="d", roles_involved=("A",), severity="LOW"
        )
        with self.assertRaises(dataclasses.FrozenInstanceError):
            rec.severity = "HIGH"


# ── (c) Bundle assembler ───────────────────────────────────────────────────────

class TestBundleAssembler(unittest.TestCase):
    def test_bundle_is_frozen(self):
        pkt = build_test_packet()
        bundle = assemble_bundle(
            packet=pkt, role_results=_all_accepted_results(),
            all_expected_role_ids=B1_ROLE_IDS,
        )
        self.assertTrue(EvidenceBundle.__dataclass_params__.frozen)
        with self.assertRaises(dataclasses.FrozenInstanceError):
            bundle.bundle_status = "HACKED"

    def test_complete_when_all_accepted_no_high(self):
        pkt = build_test_packet()
        bundle = assemble_bundle(
            packet=pkt, role_results=_all_accepted_results(),
            all_expected_role_ids=B1_ROLE_IDS,
        )
        self.assertEqual(bundle.bundle_status, BundleStatus.COMPLETE)
        self.assertEqual(set(bundle.accepted_role_ids), set(B1_ROLE_IDS))
        self.assertEqual(bundle.failed_role_ids, ())
        self.assertEqual(bundle.missing_role_ids, ())

    def test_high_contradiction_blocks_complete(self):
        pkt = build_test_packet()
        high = ContradictionRecord(
            rule_id="R", description="d", roles_involved=("A",), severity="HIGH"
        )
        bundle = assemble_bundle(
            packet=pkt, role_results=_all_accepted_results(),
            all_expected_role_ids=B1_ROLE_IDS, contradictions=(high,),
        )
        self.assertEqual(bundle.bundle_status, BundleStatus.PARTIAL)

    def test_medium_contradiction_does_not_block_complete(self):
        pkt = build_test_packet()
        med = ContradictionRecord(
            rule_id="R", description="d", roles_involved=("A",), severity="MEDIUM"
        )
        bundle = assemble_bundle(
            packet=pkt, role_results=_all_accepted_results(),
            all_expected_role_ids=B1_ROLE_IDS, contradictions=(med,),
        )
        self.assertEqual(bundle.bundle_status, BundleStatus.COMPLETE)

    def test_partial_when_one_role_failed(self):
        pkt = build_test_packet()
        results = _all_accepted_results()[:-1]
        results.append(_make_result("FINAL_REFRESH", RoleRunnerStatus.INVALID))
        bundle = assemble_bundle(
            packet=pkt, role_results=results, all_expected_role_ids=B1_ROLE_IDS,
        )
        self.assertEqual(bundle.bundle_status, BundleStatus.PARTIAL)
        self.assertIn("FINAL_REFRESH", bundle.failed_role_ids)

    def test_failed_when_zero_accepted(self):
        pkt = build_test_packet()
        results = [
            _make_result(rid, RoleRunnerStatus.RUNNER_FAILED)
            for rid in B1_ROLE_IDS
        ]
        bundle = assemble_bundle(
            packet=pkt, role_results=results, all_expected_role_ids=B1_ROLE_IDS,
        )
        self.assertEqual(bundle.bundle_status, BundleStatus.FAILED)

    def test_missing_roles_preserved_explicitly(self):
        pkt = build_test_packet()
        results = [_make_result("NEWS_STATUS")]
        bundle = assemble_bundle(
            packet=pkt, role_results=results, all_expected_role_ids=B1_ROLE_IDS,
        )
        self.assertEqual(len(bundle.missing_role_ids), len(B1_ROLE_IDS) - 1)
        self.assertNotIn("NEWS_STATUS", bundle.missing_role_ids)
        self.assertEqual(bundle.bundle_status, BundleStatus.PARTIAL)

    def test_identity_copied_from_packet(self):
        pkt = build_test_packet()
        bundle = assemble_bundle(
            packet=pkt, role_results=_all_accepted_results(),
            all_expected_role_ids=B1_ROLE_IDS,
        )
        self.assertEqual(bundle.run_id, pkt.run_id)
        self.assertEqual(bundle.snapshot_id, pkt.snapshot_id)
        self.assertEqual(bundle.canonical_event_id, pkt.canonical_event_id)
        self.assertEqual(bundle.lane, pkt.lane)

    def test_deterministic_with_injected_timestamp(self):
        pkt = build_test_packet()
        kw = dict(packet=pkt, role_results=_all_accepted_results(),
                  all_expected_role_ids=B1_ROLE_IDS,
                  assembled_at="2026-08-16T00:00:00+00:00")
        self.assertEqual(assemble_bundle(**kw), assemble_bundle(**kw))

    def test_skipped_resumed_counts_accepted_but_has_no_findings(self):
        pkt = build_test_packet()
        results = _all_accepted_results()[:-1]
        results.append(
            _make_result("FINAL_REFRESH", RoleRunnerStatus.SKIPPED_RESUMED)
        )
        bundle = assemble_bundle(
            packet=pkt, role_results=results, all_expected_role_ids=B1_ROLE_IDS,
        )
        self.assertEqual(bundle.bundle_status, BundleStatus.COMPLETE)
        self.assertIn("FINAL_REFRESH", bundle.accepted_role_ids)
        self.assertNotIn("FINAL_REFRESH", bundle.accepted_findings)

    def test_bundle_dict_contains_no_forbidden_keys(self):
        pkt = build_test_packet()
        bundle = assemble_bundle(
            packet=pkt, role_results=_all_accepted_results(),
            all_expected_role_ids=B1_ROLE_IDS,
        )
        self.assertIsNone(_scan_forbidden_keys(bundle.to_dict()))


# ── (d)+(e) Orchestrator ───────────────────────────────────────────────────────

class TestOrchestrator(unittest.TestCase):
    def test_full_run_all_roles_accepted(self):
        pkt = build_test_packet()
        runners, mock = _full_mock_runners()
        result = run_orchestrator(pkt, build_b1_registry(), runners, db_conn=None)
        self.assertIsInstance(result, OrchestratorResult)
        self.assertEqual(result.accepted_count(), 6)
        self.assertEqual(result.failed_count(), 0)
        self.assertEqual(result.bundle.bundle_status, BundleStatus.COMPLETE)
        self.assertFalse(result.persisted)

    def test_orchestrator_result_frozen(self):
        pkt = build_test_packet()
        runners, _ = _full_mock_runners()
        result = run_orchestrator(pkt, build_b1_registry(), runners, db_conn=None)
        with self.assertRaises(dataclasses.FrozenInstanceError):
            result.persisted = True

    def test_no_execution_authority_anywhere(self):
        # OrchestratorResult grants no execution authority: it exposes no
        # can_execute field and its full serialized bundle carries no
        # forbidden governance keys — unconditionally.
        pkt = build_test_packet()
        runners, _ = _full_mock_runners()
        result = run_orchestrator(pkt, build_b1_registry(), runners, db_conn=None)
        field_names = {f.name for f in dataclasses.fields(OrchestratorResult)}
        for banned in ("can_execute", "final_decision", "stake_tier",
                       "terminal_label", "capital_allocation"):
            self.assertNotIn(banned, field_names)
        self.assertIsNone(_scan_forbidden_keys(result.bundle.to_dict()))
        for rr in result.role_results:
            self.assertIsNone(_scan_forbidden_keys(rr.to_dict()))

    def test_same_packet_object_passed_to_all_runners(self):
        pkt = build_test_packet()
        runners, mock = _full_mock_runners()
        run_orchestrator(pkt, build_b1_registry(), runners, db_conn=None)
        ids = mock.packet_ids_seen()
        self.assertEqual(len(ids), 6)
        self.assertEqual(set(ids), {id(pkt)})

    def test_non_packet_input_raises_typeerror(self):
        runners, _ = _full_mock_runners()
        with self.assertRaises(TypeError):
            run_orchestrator({"not": "a packet"}, build_b1_registry(),
                             runners, db_conn=None)

    def test_missing_runner_fails_closed_no_runner(self):
        pkt = build_test_packet()
        runners, _ = _full_mock_runners()
        # Drop one runner entirely.
        dropped = ALL_B1_ENTRIES[0].agent_id
        del runners[dropped]
        result = run_orchestrator(pkt, build_b1_registry(), runners, db_conn=None)
        rr = result.result_for_agent(dropped)
        self.assertEqual(rr.status, RoleRunnerStatus.NO_RUNNER)
        self.assertEqual(result.bundle.bundle_status, BundleStatus.PARTIAL)

    def test_runner_exception_becomes_runner_failed(self):
        pkt = build_test_packet()
        presets = {e.agent_id: _PAYLOAD_BY_ROLE[e.role]() for e in ALL_B1_ENTRIES}
        presets[ALL_B1_ENTRIES[0].agent_id] = RuntimeError("boom")
        mock = MockRoleRunner(presets)
        runners = {e.agent_id: mock for e in ALL_B1_ENTRIES}
        result = run_orchestrator(pkt, build_b1_registry(), runners, db_conn=None)
        rr = result.result_for_agent(ALL_B1_ENTRIES[0].agent_id)
        self.assertEqual(rr.status, RoleRunnerStatus.RUNNER_FAILED)
        self.assertEqual(result.accepted_count(), 5)

    def test_invalid_output_rejected(self):
        pkt = build_test_packet()
        bad = _PAYLOAD_BY_ROLE["NEWS_STATUS"]()
        bad["advisory_findings"]["player_status"] = "NOT_AN_ENUM"
        runners, _ = _full_mock_runners(overrides={"NEWS_STATUS": bad})
        result = run_orchestrator(pkt, build_b1_registry(), runners, db_conn=None)
        rr = result.result_for_role("NEWS_STATUS")
        self.assertTrue(rr.failed)

    def test_forbidden_key_output_governance_rejected(self):
        pkt = build_test_packet()
        bad = _PAYLOAD_BY_ROLE["NEWS_STATUS"]()
        bad["advisory_findings"]["can_execute"] = True
        runners, _ = _full_mock_runners(overrides={"NEWS_STATUS": bad})
        result = run_orchestrator(pkt, build_b1_registry(), runners, db_conn=None)
        rr = result.result_for_role("NEWS_STATUS")
        self.assertTrue(rr.failed)
        self.assertIsNone(rr.advisory_findings)

    def test_b1_role_ids_matches_registry(self):
        self.assertEqual(len(B1_ROLE_IDS), 6)
        self.assertEqual(set(B1_ROLE_IDS), {e.role for e in ALL_B1_ENTRIES})

    def test_result_for_role_unknown_returns_none(self):
        pkt = build_test_packet()
        runners, _ = _full_mock_runners()
        result = run_orchestrator(pkt, build_b1_registry(), runners, db_conn=None)
        self.assertIsNone(result.result_for_role("NOT_A_ROLE"))

    def test_mock_runner_missing_preset_raises(self):
        mock = MockRoleRunner({})
        pkt = build_test_packet()
        with self.assertRaises(RuntimeError):
            mock(ALL_B1_ENTRIES[0], pkt)


# ── (f) Authority constants ────────────────────────────────────────────────────

class TestB2AuthorityConstants(unittest.TestCase):
    def test_constants(self):
        self.assertIs(ua_pkg.can_execute, False)
        self.assertIs(ua_pkg.PRODUCTION_AUTHORITY, False)
        self.assertIs(ua_pkg.USER_OUTPUT_AUTHORITY, False)
        self.assertIs(ua_pkg.CAPITAL_AUTHORITY, False)
        self.assertIs(ua_pkg.NO_AUTO_PROMOTION, True)


if __name__ == "__main__":
    unittest.main()
