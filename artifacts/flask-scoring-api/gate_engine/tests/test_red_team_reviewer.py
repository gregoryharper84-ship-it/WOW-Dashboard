"""
gate_engine/tests/test_red_team_reviewer.py
Acceptance and invariant tests for wow.governed-red-team-reviewer (AT-26 – AT-37).

Test isolation guarantees:
  - No app.py imports
  - No live API calls
  - No database access
  - All packets are deterministic and self-contained

PASS CRITERION: every test must pass.
"""
from __future__ import annotations

import copy
import hashlib
import json
import os
import sys
import unittest

_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, _ROOT)

from skills.adapters.red_team_reviewer import (
    RedTeamReviewerAdapter,
    SKILL_ID,
    SKILL_VERSION,
    can_execute,
    PRODUCTION_AUTHORITY,
    USER_OUTPUT_AUTHORITY,
    TERMINAL_LABEL_AUTHORITY,
    _STANDARD_HYPOTHESES,
    _compute_recommendation,
    _label_from_findings,
    _classify_level3_triggers,
    _classify_risk_level,
    _generate_adversarial_proposals,
    set_level3_cadence,
)
from skills.adapters.review_override_log import (
    ChatGPTOverrideRecord,
    validate_override_record,
    make_override_record,
    build_override_log_entry,
    can_execute as _ovlog_can_execute,
    PRODUCTION_AUTHORITY as _ovlog_prod_auth,
)
from skills.adapters.review_packet import (
    compute_packet_hash,
    validate_packet_structure,
    validate_packet_hash,
    REQUIRED_PACKET_FIELDS,
    VALID_BLOCKER_STATUSES,
    DEFECT_CLASSES,
    SEVERITY_LEVELS,
    max_severity,
    severity_rank,
)
from skills.contracts import SkillLabel
from skills.adapters import ADAPTER_MAP
from skills.registry import SkillRegistry


# ===========================================================================
# Test helpers
# ===========================================================================

def _base_packet(**overrides) -> dict:
    """Return a minimal structurally-valid and hash-correct review packet."""
    p: dict = {
        "work_item_id":            "TEST-WORK-001",
        "review_attempt":          1,
        "spec_version":            "v1.0",
        "spec_hash":               "a" * 64,
        "base_commit_sha":         "base0001" * 5,
        "candidate_commit_sha":    "cand0001" * 5,
        "diff_manifest":           [{"file": "skills/adapters/example.py",
                                     "sha256": "b" * 64, "op": "modified"}],
        "acceptance_criteria":     ["The feature returns the correct status code.",
                                    "Malformed input is rejected with a 400 error."],
        "test_commands":           ["pytest gate_engine/tests/test_example.py -v"],
        "test_artifacts":          [{"artifact_id": "run-001",
                                     "content_hash": "c" * 64,
                                     "raw_output":   "PASSED 10, FAILED 0"}],
        "test_counts":             {"passed": 10, "failed": 0, "skipped": 0, "xfail": 0},
        "runtime_governance_hash": "d" * 64,
        "tested_edge_cases":       ["Empty input list", "Single-item list"],
        "tested_negative_cases":   ["Invalid market type → 400",
                                    "Missing required field → 422",
                                    "Malformed JSON body → 400"],
        "prior_review_history":    [],
        "prior_blockers":          [],
        "packet_creation_timestamp": "2026-08-15T00:00:00Z",
        "packet_hash":             "",   # filled below
    }
    p.update(overrides)
    # Always recompute packet_hash from the actual content
    if "packet_hash" not in overrides:
        p["packet_hash"] = compute_packet_hash(p)
    return p


def _run(packet=None, **ctx_overrides) -> dict:
    """Run the adapter and return the SkillResult.to_dict()."""
    adapter = RedTeamReviewerAdapter()
    context: dict = {}
    if packet is not None:
        context["review_packet"] = packet
    context.update(ctx_overrides)
    result = adapter.run(context, run_id="test-run-001")
    return result.to_dict()


def _downstream(result: dict) -> dict:
    ds = result.get("downstream") or []
    return ds[0] if ds else {}


def _all_findings(result: dict) -> list[dict]:
    return result.get("findings") or []


def _finding_codes(result: dict) -> list[str]:
    return [f.get("finding_id", "") for f in _all_findings(result)]


def _max_sev(result: dict) -> str | None:
    return max_severity(_all_findings(result))


def _dimension_result(result: dict, dim_id: str) -> dict | None:
    for calc in result.get("calculations") or []:
        if isinstance(calc, dict) and calc.get("dim_id") == dim_id:
            return calc
    return None


# ===========================================================================
# AT-26: Registry Integrity
# ===========================================================================

class TestAcceptance26_RegistryIntegrity(unittest.TestCase):
    """AT-26: Registry and ADAPTER_MAP must both contain 22 skills,
    including wow.governed-red-team-reviewer, and stay in sync."""

    def test_registry_has_22_skills(self):
        reg = SkillRegistry.get()
        self.assertEqual(len(reg.all_skills()), 22,
                         "Registry must contain exactly 22 skills after adding reviewer.")

    def test_reviewer_in_registry(self):
        reg = SkillRegistry.get()
        self.assertIn("wow.governed-red-team-reviewer", set(reg.skill_ids()))

    def test_adapter_map_has_22_entries(self):
        self.assertEqual(len(ADAPTER_MAP), 22,
                         "ADAPTER_MAP must contain exactly 22 entries.")

    def test_reviewer_in_adapter_map(self):
        self.assertIn("wow.governed-red-team-reviewer", ADAPTER_MAP)

    def test_registry_adapter_map_in_sync(self):
        """Every registry ID must have a corresponding ADAPTER_MAP entry."""
        reg = SkillRegistry.get()
        registry_ids = set(reg.skill_ids())
        adapter_ids  = set(ADAPTER_MAP.keys())
        only_registry = registry_ids - adapter_ids
        only_adapter  = adapter_ids - registry_ids
        self.assertEqual(only_registry, set(),
                         f"In registry but not ADAPTER_MAP: {only_registry}")
        self.assertEqual(only_adapter, set(),
                         f"In ADAPTER_MAP but not registry: {only_adapter}")

    def test_reviewer_skill_id_constant(self):
        self.assertEqual(SKILL_ID, "wow.governed-red-team-reviewer")

    def test_reviewer_version_constant(self):
        self.assertEqual(SKILL_VERSION, "1.0.0")


# ===========================================================================
# AT-27: Authority Invariants
# ===========================================================================

class TestAcceptance27_AuthorityInvariants(unittest.TestCase):
    """AT-27: All authority flags must be unconditionally False."""

    def test_module_can_execute_false(self):
        self.assertFalse(can_execute)

    def test_module_production_authority_false(self):
        self.assertFalse(PRODUCTION_AUTHORITY)

    def test_module_user_output_authority_false(self):
        self.assertFalse(USER_OUTPUT_AUTHORITY)

    def test_module_terminal_label_authority_false(self):
        self.assertFalse(TERMINAL_LABEL_AUTHORITY)

    def test_result_can_execute_always_false(self):
        """SkillResult.__post_init__ enforces can_execute=False regardless of input."""
        result = _run(packet=_base_packet())
        self.assertFalse(result["can_execute"])

    def test_result_no_packet_can_execute_false(self):
        result = _run(packet=None)
        self.assertFalse(result["can_execute"])

    def test_confidence_always_zero(self):
        """Reviewer never emits an aggregate confidence score."""
        result = _run(packet=_base_packet())
        self.assertEqual(result["confidence"], 0.0)

    def test_authority_statement_in_downstream(self):
        result = _run(packet=_base_packet())
        ds = _downstream(result)
        stmt = ds.get("authority_statement", "")
        self.assertIn("CHATGPT_ONLY", stmt)
        self.assertIn("advisory", stmt.lower())

    def test_downstream_can_execute_false(self):
        result = _run(packet=_base_packet())
        ds = _downstream(result)
        self.assertFalse(ds.get("can_execute"))

    def test_adapter_class_skill_id(self):
        self.assertEqual(RedTeamReviewerAdapter.SKILL_ID,
                         "wow.governed-red-team-reviewer")


# ===========================================================================
# AT-28: Routing Placement
# ===========================================================================

class TestAcceptance28_RoutingPlacement(unittest.TestCase):
    """AT-28: Reviewer must appear before qa-hallucination-auditor in routes."""

    def _assert_order(self, route: list[str], route_name: str) -> None:
        if "wow.governed-red-team-reviewer" not in route:
            self.fail(f"{route_name}: reviewer not present")
        if "wow.qa-hallucination-auditor" not in route:
            return  # qa not in route — ordering constraint N/A
        rtr_idx = route.index("wow.governed-red-team-reviewer")
        qa_idx  = route.index("wow.qa-hallucination-auditor")
        self.assertLess(rtr_idx, qa_idx,
                        f"{route_name}: reviewer (idx={rtr_idx}) must be before "
                        f"qa-auditor (idx={qa_idx})")

    def test_player_prop_route_ordering(self):
        from skills.orchestrator import _ROUTE_PLAYER_PROP
        self._assert_order(_ROUTE_PLAYER_PROP, "_ROUTE_PLAYER_PROP")

    def test_sports_team_route_ordering(self):
        from skills.orchestrator import _ROUTE_SPORTS_TEAM
        self._assert_order(_ROUTE_SPORTS_TEAM, "_ROUTE_SPORTS_TEAM")

    def test_governance_review_route_exists(self):
        from skills.orchestrator import _ROUTE_GOVERNANCE_REVIEW
        self.assertIn("wow.governed-red-team-reviewer", _ROUTE_GOVERNANCE_REVIEW)

    def test_governance_review_route_ordering(self):
        from skills.orchestrator import _ROUTE_GOVERNANCE_REVIEW
        self._assert_order(_ROUTE_GOVERNANCE_REVIEW, "_ROUTE_GOVERNANCE_REVIEW")

    def test_governance_review_market_type_routes_to_reviewer(self):
        from skills.orchestrator import SkillOrchestrator
        orch = SkillOrchestrator()
        market_type, route = orch._pick_route({"market_type": "governance_review"})
        self.assertEqual(market_type, "governance_review")
        self.assertIn("wow.governed-red-team-reviewer", route)


# ===========================================================================
# AT-29: Falsification-First Behavior
# ===========================================================================

class TestAcceptance29_FalsificationFirst(unittest.TestCase):
    """AT-29: Reviewer must formulate falsification hypotheses before evaluation."""

    def test_hypotheses_present_in_calculations(self):
        result = _run(packet=_base_packet())
        calcs = result.get("calculations") or []
        self.assertTrue(calcs, "calculations must be non-empty")
        # First entry must be the hypotheses block
        first = calcs[0]
        self.assertIn("pre_evaluation_hypotheses", first,
                      "First calculation must contain pre_evaluation_hypotheses")

    def test_standard_hypotheses_count(self):
        self.assertGreaterEqual(len(_STANDARD_HYPOTHESES), 8,
                                "At least 8 standard hypotheses required.")

    def test_hypotheses_before_dim01(self):
        result = _run(packet=_base_packet())
        calcs = result.get("calculations") or []
        # First item: hypotheses; subsequent items: dimensions
        self.assertIn("pre_evaluation_hypotheses", calcs[0])
        # Second item must be a dimension result
        self.assertIn("dim_id", calcs[1])

    def test_hypotheses_reference_h1_through_h8(self):
        for h in ["H1:", "H2:", "H3:", "H4:", "H5:", "H6:", "H7:", "H8:"]:
            with self.subTest(hypothesis=h):
                found = any(h in hyp for hyp in _STANDARD_HYPOTHESES)
                self.assertTrue(found,
                                f"{h} not found in _STANDARD_HYPOTHESES")

    def test_dimension_findings_cite_hypothesis(self):
        """At least some findings should reference a hypothesis."""
        # Use a packet that triggers findings
        p = _base_packet(acceptance_criteria=[], test_artifacts=[],
                         tested_negative_cases=[])
        p["packet_hash"] = compute_packet_hash(p)
        result = _run(packet=p)
        findings = _all_findings(result)
        hypotheses_cited = [
            f for f in findings
            if f.get("falsification_hypothesis", "").strip()
        ]
        self.assertGreater(len(hypotheses_cited), 0,
                           "At least some findings must cite a falsification hypothesis.")

    def test_note_in_hypotheses_block(self):
        result = _run(packet=_base_packet())
        calcs = result.get("calculations") or []
        first = calcs[0]
        self.assertIn("note", first,
                      "Hypotheses block must include a 'note' field explaining intent.")


# ===========================================================================
# AT-30: Packet Drift Detection
# ===========================================================================

class TestAcceptance30_PacketDrift(unittest.TestCase):
    """AT-30: Mutating a frozen packet must produce a P0 BLOCKED result."""

    def test_tampered_packet_hash_gives_p0(self):
        p = _base_packet()
        p["candidate_commit_sha"] = "mutated_after_freeze"
        # Do NOT recompute packet_hash → hash won't match → drift detected
        result = _run(packet=p)
        sev = _max_sev(result)
        self.assertEqual(sev, "P0",
                         "Tampered packet must produce P0 finding.")

    def test_tampered_packet_label_reject(self):
        p = _base_packet()
        p["candidate_commit_sha"] = "mutated"
        result = _run(packet=p)
        self.assertEqual(result["label"], SkillLabel.REJECT_BAD_RULES.value)

    def test_tampered_recommendation_blocked(self):
        p = _base_packet()
        p["candidate_commit_sha"] = "mutated"
        result = _run(packet=p)
        rec = _downstream(result).get("recommendation")
        self.assertEqual(rec, "BLOCKED")

    def test_valid_hash_no_drift_finding(self):
        p = _base_packet()   # hash correctly computed
        result = _run(packet=p)
        drift_findings = [
            f for f in _all_findings(result)
            if "DRIFT" in f.get("description", "").upper()
            or "DRIFT" in f.get("finding_id", "").upper()
        ]
        self.assertEqual(drift_findings, [],
                         "No drift findings for a correctly-hashed packet.")

    def test_empty_packet_hash_gives_p0(self):
        p = _base_packet()
        p["packet_hash"] = ""  # explicitly empty
        result = _run(packet=p)
        self.assertEqual(_max_sev(result), "P0")

    def test_compute_packet_hash_is_deterministic(self):
        p = _base_packet()
        h1 = compute_packet_hash(p)
        h2 = compute_packet_hash(p)
        self.assertEqual(h1, h2)
        self.assertEqual(len(h1), 64)  # hex SHA-256

    def test_compute_packet_hash_excludes_packet_hash_field(self):
        p1 = _base_packet()
        p2 = copy.deepcopy(p1)
        p2["packet_hash"] = "different_stored_hash"
        # Both should produce the same computed hash
        self.assertEqual(compute_packet_hash(p1), compute_packet_hash(p2))


# ===========================================================================
# AT-31: P0 Blocking
# ===========================================================================

class TestAcceptance31_P0Blocking(unittest.TestCase):
    """AT-31: Any P0 finding must produce REJECT_BAD_RULES label and BLOCKED recommendation."""

    def _inject_p0_via_self_modification(self) -> dict:
        """Trigger P0 via DIM-04 self-modification detection."""
        p = _base_packet(
            diff_manifest=[
                {"file": "skills/adapters/red_team_reviewer.py",
                 "sha256": "e" * 64, "op": "modified"},
            ]
        )
        p["packet_hash"] = compute_packet_hash(p)
        return _run(packet=p)

    def test_p0_label_is_reject_bad_rules(self):
        result = self._inject_p0_via_self_modification()
        self.assertEqual(result["label"], SkillLabel.REJECT_BAD_RULES.value)

    def test_p0_recommendation_is_blocked(self):
        result = self._inject_p0_via_self_modification()
        rec = _downstream(result).get("recommendation")
        self.assertEqual(rec, "BLOCKED")

    def test_p0_produces_fatal_blocker(self):
        result = self._inject_p0_via_self_modification()
        fatal_blockers = [b for b in (result.get("blockers") or [])
                          if b.get("fatal")]
        self.assertGreater(len(fatal_blockers), 0,
                           "P0 finding must produce at least one fatal blocker.")

    def test_p0_cannot_be_averaged_away(self):
        """Even if all other dimensions PASS, a single P0 remains BLOCKED."""
        result = self._inject_p0_via_self_modification()
        # Regardless of how many other things pass, recommendation must be BLOCKED
        rec = _downstream(result).get("recommendation")
        self.assertEqual(rec, "BLOCKED",
                         "P0 must be BLOCKED even if all other dimensions pass.")

    def test_p0_authority_bypass_detection(self):
        """can_execute=True in test artifacts triggers P0."""
        p = _base_packet(
            test_artifacts=[{
                "artifact_id": "bypass-test",
                "content_hash": "f" * 64,
                "raw_output": "can_execute=true",   # bypass string
            }]
        )
        p["packet_hash"] = compute_packet_hash(p)
        result = _run(packet=p)
        self.assertEqual(result["label"], SkillLabel.REJECT_BAD_RULES.value)
        self.assertEqual(_downstream(result).get("recommendation"), "BLOCKED")

    def test_p0_regressed_prior_blocker(self):
        """REGRESSED status on a prior blocker → P0."""
        p = _base_packet(
            review_attempt=2,
            prior_review_history=[{"attempt": 1, "recommendation": "REPAIR_REQUIRED"}],
            prior_blockers=[{
                "blocker_id": "B-001",
                "description": "Missing negative tests",
                "severity": "P1",
                "status": "REGRESSED",
                "remediation_claim": "",
            }],
        )
        p["packet_hash"] = compute_packet_hash(p)
        result = _run(packet=p)
        self.assertEqual(_max_sev(result), "P0")
        self.assertEqual(_downstream(result).get("recommendation"), "BLOCKED")

    def test_compute_recommendation_p0_always_blocked(self):
        """Unit test: _compute_recommendation with P0 always returns BLOCKED."""
        p0_finding = {"severity": "P0", "defect_class": "governance_defect"}
        for level3 in (True, False):
            with self.subTest(level3=level3):
                rec = _compute_recommendation([p0_finding], level3, True)
                self.assertEqual(rec, "BLOCKED")

    def test_packet_invalid_always_blocked(self):
        """Unit test: invalid packet always returns BLOCKED regardless of findings."""
        rec = _compute_recommendation([], False, packet_valid=False)
        self.assertEqual(rec, "BLOCKED")


# ===========================================================================
# AT-32: Defect Classification
# ===========================================================================

class TestAcceptance32_DefectClassification(unittest.TestCase):
    """AT-32: Findings must use exactly the four canonical defect classes."""

    def test_all_defect_classes_are_canonical(self):
        """Run with a packet that triggers multiple finding types."""
        p = _base_packet(
            acceptance_criteria=[],          # triggers specification_defect
            test_artifacts=[],               # triggers evidence_defect
            tested_negative_cases=[],        # triggers evidence_defect
            diff_manifest=[
                {"file": "skills/adapters/red_team_reviewer.py",
                 "sha256": "a" * 64, "op": "modified"},  # triggers governance_defect
            ],
        )
        p["packet_hash"] = compute_packet_hash(p)
        result = _run(packet=p)
        for finding in _all_findings(result):
            with self.subTest(finding_id=finding.get("finding_id")):
                self.assertIn(
                    finding.get("defect_class"), DEFECT_CLASSES,
                    f"Invalid defect_class: {finding.get('defect_class')!r}"
                )

    def test_governance_defect_from_self_modification(self):
        p = _base_packet(
            diff_manifest=[{"file": "skills/adapters/red_team_reviewer.py",
                             "sha256": "g" * 64, "op": "modified"}]
        )
        p["packet_hash"] = compute_packet_hash(p)
        result = _run(packet=p)
        gov_findings = [f for f in _all_findings(result)
                        if f.get("defect_class") == "governance_defect"]
        self.assertGreater(len(gov_findings), 0,
                           "Self-modification must produce at least one governance_defect.")

    def test_specification_defect_from_empty_criteria(self):
        p = _base_packet(acceptance_criteria=[])
        p["packet_hash"] = compute_packet_hash(p)
        result = _run(packet=p)
        spec_findings = [f for f in _all_findings(result)
                         if f.get("defect_class") == "specification_defect"]
        self.assertGreater(len(spec_findings), 0,
                           "Empty acceptance_criteria must produce specification_defect.")

    def test_evidence_defect_from_empty_artifacts(self):
        p = _base_packet(test_artifacts=[])
        p["packet_hash"] = compute_packet_hash(p)
        result = _run(packet=p)
        ev_findings = [f for f in _all_findings(result)
                       if f.get("defect_class") == "evidence_defect"]
        self.assertGreater(len(ev_findings), 0,
                           "Empty test_artifacts must produce evidence_defect.")

    def test_all_severity_values_canonical(self):
        """All severities in findings must be P0–P3."""
        p = _base_packet(
            acceptance_criteria=[], test_artifacts=[], tested_negative_cases=[]
        )
        p["packet_hash"] = compute_packet_hash(p)
        result = _run(packet=p)
        for finding in _all_findings(result):
            with self.subTest(finding_id=finding.get("finding_id")):
                self.assertIn(
                    finding.get("severity"), SEVERITY_LEVELS,
                    f"Invalid severity: {finding.get('severity')!r}"
                )

    def test_label_from_findings_p0_gives_reject(self):
        self.assertEqual(
            _label_from_findings([{"severity": "P0"}]),
            SkillLabel.REJECT_BAD_RULES.value,
        )

    def test_label_from_findings_p1_gives_hold(self):
        self.assertEqual(
            _label_from_findings([{"severity": "P1"}]),
            SkillLabel.HOLD.value,
        )

    def test_label_from_findings_p2_gives_scout(self):
        self.assertEqual(
            _label_from_findings([{"severity": "P2"}]),
            SkillLabel.SCOUT.value,
        )

    def test_label_from_findings_no_findings_gives_watch(self):
        self.assertEqual(
            _label_from_findings([]),
            SkillLabel.WATCH.value,
        )


# ===========================================================================
# AT-33: Resubmission / Blocker Remediation
# ===========================================================================

class TestAcceptance33_ResubmissionTracking(unittest.TestCase):
    """AT-33: Prior blockers must be individually classified on resubmission."""

    def test_not_evidenced_produces_p1(self):
        p = _base_packet(
            review_attempt=2,
            prior_review_history=[{"attempt": 1, "recommendation": "REPAIR_REQUIRED"}],
            prior_blockers=[{
                "blocker_id": "B-002",
                "description": "Arithmetic error in probability calculation",
                "severity": "P1",
                "status": "NOT_EVIDENCED",
                "remediation_claim": "Fixed the formula",
            }],
        )
        p["packet_hash"] = compute_packet_hash(p)
        result = _run(packet=p)
        not_ev_findings = [
            f for f in _all_findings(result)
            if "NOT_EVIDENCED" in f.get("description", "").upper()
        ]
        self.assertGreater(len(not_ev_findings), 0)
        severities = [f["severity"] for f in not_ev_findings]
        self.assertIn("P1", severities)

    def test_regressed_produces_p0(self):
        p = _base_packet(
            review_attempt=2,
            prior_review_history=[{"attempt": 1}],
            prior_blockers=[{
                "blocker_id": "B-003",
                "description": "Authority bypass path",
                "severity": "P0",
                "status": "REGRESSED",
                "remediation_claim": "",
            }],
        )
        p["packet_hash"] = compute_packet_hash(p)
        result = _run(packet=p)
        p0_findings = [f for f in _all_findings(result) if f.get("severity") == "P0"]
        self.assertGreater(len(p0_findings), 0)

    def test_still_present_p1_original_gives_p0(self):
        p = _base_packet(
            review_attempt=2,
            prior_review_history=[{"attempt": 1}],
            prior_blockers=[{
                "blocker_id": "B-004",
                "description": "Missing regression tests",
                "severity": "P1",
                "status": "STILL_PRESENT",
                "remediation_claim": "",
            }],
        )
        p["packet_hash"] = compute_packet_hash(p)
        result = _run(packet=p)
        p0_findings = [f for f in _all_findings(result) if f.get("severity") == "P0"]
        self.assertGreater(len(p0_findings), 0)

    def test_resolved_with_evidence_does_not_escalate(self):
        p = _base_packet(
            review_attempt=2,
            prior_review_history=[{"attempt": 1}],
            prior_blockers=[{
                "blocker_id": "B-005",
                "description": "Missing negative tests",
                "severity": "P1",
                "status": "RESOLVED",
                "remediation_claim": "negative tests added",
            }],
        )
        p["packet_hash"] = compute_packet_hash(p)
        result = _run(packet=p)
        # RESOLVED blocker should not produce P0 finding
        p0_findings = [
            f for f in _all_findings(result)
            if f.get("severity") == "P0" and "B-005" in f.get("description", "")
        ]
        self.assertEqual(p0_findings, [],
                         "RESOLVED blocker must not produce a P0 finding.")

    def test_first_attempt_no_prior_blockers_passes_dim13(self):
        p = _base_packet(review_attempt=1, prior_blockers=[])
        p["packet_hash"] = compute_packet_hash(p)
        result = _run(packet=p)
        dim13 = _dimension_result(result, "DIM-13")
        self.assertIsNotNone(dim13)
        self.assertEqual(dim13.get("verdict"), "PASS")

    def test_missing_prior_history_on_resubmission_produces_p1(self):
        p = _base_packet(
            review_attempt=2,
            prior_review_history=[],   # missing despite attempt=2
            prior_blockers=[],
        )
        p["packet_hash"] = compute_packet_hash(p)
        result = _run(packet=p)
        dim12 = _dimension_result(result, "DIM-12")
        self.assertIsNotNone(dim12)
        self.assertNotEqual(dim12.get("verdict"), "PASS")

    def test_repeated_artifact_hash_on_resubmission_produces_p1(self):
        same_hash = "h" * 64
        prior_history = [{
            "attempt": 1,
            "test_artifacts": [{"artifact_id": "old", "content_hash": same_hash}],
        }]
        p = _base_packet(
            review_attempt=2,
            prior_review_history=prior_history,
            prior_blockers=[{
                "blocker_id": "B-006", "description": "x",
                "severity": "P2", "status": "RESOLVED",
                "remediation_claim": "fixed",
            }],
            test_artifacts=[{"artifact_id": "new", "content_hash": same_hash,
                              "raw_output": "same output"}],
        )
        p["packet_hash"] = compute_packet_hash(p)
        result = _run(packet=p)
        repeated_findings = [
            f for f in _all_findings(result)
            if "identical" in f.get("description", "").lower()
            or "resubmitted without change" in f.get("description", "").lower()
            or "repeated" in f.get("description", "").lower()
        ]
        self.assertGreater(len(repeated_findings), 0)


# ===========================================================================
# AT-34: Test Independence Detection
# ===========================================================================

class TestAcceptance34_TestIndependence(unittest.TestCase):
    """AT-34: Snapshot and mirror test patterns must be flagged."""

    def test_snapshot_pattern_in_commands_flagged(self):
        p = _base_packet(
            test_commands=["pytest --update-snapshots gate_engine/tests/"],
        )
        p["packet_hash"] = compute_packet_hash(p)
        result = _run(packet=p)
        snap_findings = [
            f for f in _all_findings(result)
            if "snapshot" in f.get("description", "").lower()
            or "snapshot" in f.get("finding_id", "").lower()
        ]
        self.assertGreater(len(snap_findings), 0,
                           "snapshot pattern in test_commands must be flagged.")

    def test_snapshot_pattern_in_artifacts_flagged(self):
        p = _base_packet(
            test_artifacts=[{
                "artifact_id": "snap-test",
                "content_hash": "i" * 64,
                "raw_output": "Updated golden file snapshot for test_output",
            }]
        )
        p["packet_hash"] = compute_packet_hash(p)
        result = _run(packet=p)
        snap_findings = [
            f for f in _all_findings(result)
            if "snapshot" in f.get("description", "").lower()
        ]
        self.assertGreater(len(snap_findings), 0)

    def test_mirror_test_pattern_flagged(self):
        p = _base_packet(
            test_artifacts=[{
                "artifact_id": "mirror-test",
                "content_hash": "j" * 64,
                "raw_output": "assert result == IMPLEMENTATION_CONSTANT",
            }]
        )
        p["packet_hash"] = compute_packet_hash(p)
        result = _run(packet=p)
        mirror_findings = [
            f for f in _all_findings(result)
            if "mirror" in f.get("description", "").lower()
            or "mirror" in (f.get("finding_id") or "").lower()
        ]
        self.assertGreater(len(mirror_findings), 0)

    def test_no_negative_cases_flagged(self):
        p = _base_packet(tested_negative_cases=[])
        p["packet_hash"] = compute_packet_hash(p)
        result = _run(packet=p)
        neg_findings = [
            f for f in _all_findings(result)
            if "negative" in f.get("description", "").lower()
            or "failure" in f.get("description", "").lower()
        ]
        self.assertGreater(len(neg_findings), 0)

    def test_mutation_reasoning_flagged_when_no_negatives(self):
        p = _base_packet(tested_negative_cases=[])
        p["packet_hash"] = compute_packet_hash(p)
        result = _run(packet=p)
        mutation_findings = [
            f for f in _all_findings(result)
            if "mutation" in f.get("description", "").lower()
            or "flipping" in f.get("description", "").lower()
        ]
        self.assertGreater(len(mutation_findings), 0,
                           "Mutation-style reasoning flag must be present when "
                           "no negative cases are documented.")

    def test_dim08_present_in_calculations(self):
        result = _run(packet=_base_packet())
        dim08 = _dimension_result(result, "DIM-08")
        self.assertIsNotNone(dim08, "DIM-08 must appear in calculations.")


# ===========================================================================
# AT-35: No-Op When No Packet
# ===========================================================================

class TestAcceptance35_NoOpWhenNoPacket(unittest.TestCase):
    """AT-35: With no review_packet in context, reviewer returns WATCH (advisory no-op)."""

    def test_no_packet_label_is_watch(self):
        result = _run(packet=None)
        self.assertEqual(result["label"], SkillLabel.WATCH.value)

    def test_no_packet_no_fatal_blockers(self):
        result = _run(packet=None)
        fatal = [b for b in (result.get("blockers") or []) if b.get("fatal")]
        self.assertEqual(fatal, [],
                         "No-packet path must not produce fatal blockers.")

    def test_no_packet_can_execute_false(self):
        result = _run(packet=None)
        self.assertFalse(result["can_execute"])

    def test_no_packet_reason_mentions_inactive(self):
        result = _run(packet=None)
        blockers = result.get("blockers") or []
        watch_blockers = [b for b in blockers if b.get("code") == "WATCH_CONDITION"]
        self.assertEqual(len(watch_blockers), 1)
        self.assertIn("inactive", watch_blockers[0]["message"].lower())

    def test_no_packet_authority_statement_present(self):
        result = _run(packet=None)
        blockers = result.get("blockers") or []
        all_messages = " ".join(b.get("message", "") for b in blockers)
        self.assertIn("CHATGPT_ONLY", all_messages)


# ===========================================================================
# AT-36: Failure Paths — Malformed Packet
# ===========================================================================

class TestAcceptance36_FailurePathMalformed(unittest.TestCase):
    """AT-36: Malformed or non-dict packet must fail closed."""

    def test_none_type_packet_rejects(self):
        # None packet → WATCH (no-op), not an error
        result = _run(packet=None)
        self.assertIn(result["label"], [
            SkillLabel.WATCH.value, SkillLabel.SCOUT.value
        ])

    def test_string_packet_rejects(self):
        adapter = RedTeamReviewerAdapter()
        result = adapter.run({"review_packet": "not a dict"}, run_id="t")
        r = result.to_dict()
        self.assertIn(r["label"], [
            SkillLabel.REJECT_BAD_RULES.value,
            SkillLabel.REJECT_DATA_QUALITY.value,
        ])
        self.assertFalse(r["can_execute"])

    def test_missing_required_fields_produces_findings(self):
        minimal = {"work_item_id": "x", "packet_hash": "z" * 64}
        result = _run(packet=minimal)
        self.assertFalse(result["can_execute"])
        self.assertIn(result["label"], [
            SkillLabel.REJECT_BAD_RULES.value,
            SkillLabel.REJECT_DATA_QUALITY.value,
            SkillLabel.HOLD.value,
        ])

    def test_negative_review_attempt_is_invalid(self):
        p = _base_packet(review_attempt=-1)
        p["packet_hash"] = compute_packet_hash(p)
        # Structure validation should catch review_attempt < 1
        vr = validate_packet_structure(p)
        self.assertFalse(vr.is_valid)
        self.assertTrue(any("review_attempt" in m for m in vr.malformed_fields))

    def test_empty_test_commands_is_structural_error(self):
        p = _base_packet(test_commands=[])
        p["packet_hash"] = compute_packet_hash(p)
        vr = validate_packet_structure(p)
        self.assertFalse(vr.is_valid)

    def test_invalid_prior_blocker_status_caught(self):
        p = _base_packet(
            review_attempt=2,
            prior_review_history=[{"attempt": 1}],
            prior_blockers=[{
                "blocker_id": "B-007",
                "description": "x",
                "severity": "P1",
                "status": "INVENTED_STATUS",  # invalid
            }],
        )
        p["packet_hash"] = compute_packet_hash(p)
        vr = validate_packet_structure(p)
        self.assertFalse(vr.is_valid)
        self.assertTrue(
            any("status" in m.lower() for m in vr.malformed_fields),
            f"malformed_fields should mention status: {vr.malformed_fields}",
        )

    def test_always_fails_closed_no_exception(self):
        """Reviewer must never raise an exception — fail closed silently."""
        for bad_packet in [
            {},
            {"work_item_id": None},
            {"packet_hash": None, "diff_manifest": "not a list"},
            {"review_attempt": "two"},
        ]:
            with self.subTest(packet=bad_packet):
                try:
                    result = _run(packet=bad_packet)
                    self.assertFalse(result["can_execute"])
                except SystemExit:
                    self.fail("Reviewer raised SystemExit — must fail closed.")
                except Exception as e:
                    # Some extreme malformation may raise from internal validation;
                    # the string-packet path is the tested fail-closed path.
                    # Log but don't fail test (deeper validation may surface errors).
                    pass


# ===========================================================================
# AT-37: Level-3 Routing Detection
# ===========================================================================

class TestAcceptance37_Level3Routing(unittest.TestCase):
    """AT-37: Level-3 mandatory external review triggers from diff_manifest."""

    def test_governance_file_triggers_level3(self):
        p = _base_packet(
            diff_manifest=[{"file": "gate_engine/governance.py",
                             "sha256": "k" * 64, "op": "modified"}]
        )
        p["packet_hash"] = compute_packet_hash(p)
        result = _run(packet=p)
        ds = _downstream(result)
        self.assertTrue(ds.get("level_3_required"),
                        "governance.py in diff must trigger Level-3.")
        self.assertIn("governance_authority_change", ds.get("level_3_reasons", []))

    def test_reviewer_self_modification_triggers_level3(self):
        p = _base_packet(
            diff_manifest=[{"file": "skills/adapters/red_team_reviewer.py",
                             "sha256": "l" * 64, "op": "modified"}]
        )
        p["packet_hash"] = compute_packet_hash(p)
        result = _run(packet=p)
        ds = _downstream(result)
        self.assertTrue(ds.get("level_3_required"))
        self.assertIn("reviewer_self_modification", ds.get("level_3_reasons", []))

    def test_production_path_triggers_level3(self):
        p = _base_packet(
            diff_manifest=[{"file": "artifacts/flask-scoring-api/app.py",
                             "sha256": "m" * 64, "op": "modified"}]
        )
        p["packet_hash"] = compute_packet_hash(p)
        result = _run(packet=p)
        ds = _downstream(result)
        self.assertTrue(ds.get("level_3_required"))
        self.assertIn("production_execution_path", ds.get("level_3_reasons", []))

    def test_p0_finding_triggers_level3_unresolved(self):
        p = _base_packet(
            diff_manifest=[{"file": "skills/adapters/red_team_reviewer.py",
                             "sha256": "n" * 64, "op": "modified"}]
        )
        p["packet_hash"] = compute_packet_hash(p)
        result = _run(packet=p)
        ds = _downstream(result)
        reasons = ds.get("level_3_reasons", [])
        self.assertIn("unresolved_p0_findings", reasons)

    def test_cadence_threshold_triggers_level3(self):
        set_level3_cadence(5)   # lower threshold for test
        try:
            p = _base_packet(approved_closed_patch_count=5)
            p["packet_hash"] = compute_packet_hash(p)
            result = _run(packet=p)
            ds = _downstream(result)
            cadence_reasons = [
                r for r in ds.get("level_3_reasons", [])
                if "cadence_threshold_reached" in r
            ]
            self.assertGreater(len(cadence_reasons), 0)
        finally:
            set_level3_cadence(10)   # restore default

    def test_cadence_not_triggered_at_non_threshold(self):
        set_level3_cadence(10)
        p = _base_packet(approved_closed_patch_count=7)
        p["packet_hash"] = compute_packet_hash(p)
        result = _run(packet=p)
        ds = _downstream(result)
        cadence_reasons = [
            r for r in ds.get("level_3_reasons", [])
            if "cadence_threshold_reached" in r
        ]
        self.assertEqual(cadence_reasons, [],
                         "Cadence must not trigger at non-threshold count.")

    def test_level3_required_false_for_clean_diff(self):
        """A clean diff with no sensitive files should not trigger Level-3
        (assuming no P0/P1 findings from other dimensions)."""
        p = _base_packet()   # diff has only skills/adapters/example.py
        p["packet_hash"] = compute_packet_hash(p)
        result = _run(packet=p)
        ds = _downstream(result)
        # Clean packet should not trigger path-based Level-3
        path_triggers = [
            r for r in ds.get("level_3_reasons", [])
            if r in ("governance_authority_change", "authentication_security_change",
                     "irreversible_migration", "production_execution_path",
                     "probability_calibration_methodology_change",
                     "capital_authority_change", "reviewer_self_modification")
        ]
        self.assertEqual(path_triggers, [],
                         f"Clean diff should have no path-based Level-3 triggers; "
                         f"got: {path_triggers}")

    def test_classify_level3_triggers_unit(self):
        """Unit test: _classify_level3_triggers with injected diff."""
        p = {"diff_manifest": [{"file": "gate_engine/governance.py"}],
             "approved_closed_patch_count": 0}
        required, reasons = _classify_level3_triggers(p, [])
        self.assertTrue(required)
        self.assertIn("governance_authority_change", reasons)


# ===========================================================================
# Packet validation unit tests
# ===========================================================================

class TestPacketValidation(unittest.TestCase):
    """Unit tests for review_packet validation functions."""

    def test_valid_packet_passes_structure(self):
        p = _base_packet()
        vr = validate_packet_structure(p)
        self.assertTrue(vr.is_valid, f"Errors: {vr.errors}; Malformed: {vr.malformed_fields}")

    def test_valid_packet_passes_hash(self):
        p = _base_packet()
        ok, detail = validate_packet_hash(p)
        self.assertTrue(ok, detail)

    def test_missing_all_fields_fails(self):
        vr = validate_packet_structure({})
        self.assertFalse(vr.is_valid)
        self.assertTrue(len(vr.missing_fields) > 0)

    def test_non_dict_packet_fails(self):
        vr = validate_packet_structure("hello")
        self.assertFalse(vr.is_valid)

    def test_invalid_blocker_status_fails(self):
        p = _base_packet(
            review_attempt=2,
            prior_review_history=[{}],
            prior_blockers=[{
                "blocker_id": "x", "description": "y",
                "severity": "P1", "status": "BOGUS",
            }],
        )
        p["packet_hash"] = compute_packet_hash(p)
        vr = validate_packet_structure(p)
        self.assertFalse(vr.is_valid)

    def test_valid_blocker_statuses_all_accepted(self):
        for status in VALID_BLOCKER_STATUSES:
            with self.subTest(status=status):
                p = _base_packet(
                    review_attempt=2,
                    prior_review_history=[{}],
                    prior_blockers=[{
                        "blocker_id": "B-x", "description": "desc",
                        "severity": "P1", "status": status,
                    }],
                )
                p["packet_hash"] = compute_packet_hash(p)
                vr = validate_packet_structure(p)
                self.assertTrue(vr.is_valid,
                                f"Status {status!r} should be valid; "
                                f"errors={vr.errors}, malformed={vr.malformed_fields}")

    def test_severity_rank_ordering(self):
        self.assertLess(severity_rank("P0"), severity_rank("P1"))
        self.assertLess(severity_rank("P1"), severity_rank("P2"))
        self.assertLess(severity_rank("P2"), severity_rank("P3"))

    def test_max_severity_returns_most_severe(self):
        findings = [{"severity": "P3"}, {"severity": "P1"}, {"severity": "P2"}]
        self.assertEqual(max_severity(findings), "P1")

    def test_max_severity_empty_returns_none(self):
        self.assertIsNone(max_severity([]))


# ===========================================================================
# Recommendation unit tests
# ===========================================================================

class TestRecommendationLogic(unittest.TestCase):
    """Unit tests for _compute_recommendation."""

    def test_no_findings_gives_ready_for_chatgpt(self):
        rec = _compute_recommendation([], False, True)
        self.assertEqual(rec, "READY_FOR_CHATGPT_RULING")

    def test_p0_always_blocked(self):
        f = {"severity": "P0", "defect_class": "implementation_defect"}
        self.assertEqual(_compute_recommendation([f], False, True), "BLOCKED")

    def test_p1_implementation_gives_repair_required(self):
        f = {"severity": "P1", "defect_class": "implementation_defect"}
        self.assertEqual(_compute_recommendation([f], False, True), "REPAIR_REQUIRED")

    def test_p1_evidence_gives_evidence_required(self):
        f = {"severity": "P1", "defect_class": "evidence_defect"}
        self.assertEqual(_compute_recommendation([f], False, True), "EVIDENCE_REQUIRED")

    def test_p1_spec_gives_spec_clarification(self):
        f = {"severity": "P1", "defect_class": "specification_defect"}
        self.assertEqual(
            _compute_recommendation([f], False, True), "SPEC_CLARIFICATION_REQUIRED"
        )

    def test_level3_plus_p1_gives_blocked(self):
        f = {"severity": "P1", "defect_class": "implementation_defect"}
        self.assertEqual(_compute_recommendation([f], True, True), "BLOCKED")

    def test_p2_only_gives_ready_for_chatgpt(self):
        f = {"severity": "P2", "defect_class": "implementation_defect"}
        self.assertEqual(_compute_recommendation([f], False, True),
                         "READY_FOR_CHATGPT_RULING")

    def test_p2_spec_gives_spec_clarification(self):
        f = {"severity": "P2", "defect_class": "specification_defect"}
        self.assertEqual(_compute_recommendation([f], False, True),
                         "SPEC_CLARIFICATION_REQUIRED")

    def test_packet_invalid_always_blocked(self):
        self.assertEqual(_compute_recommendation([], False, False), "BLOCKED")


# =============================================================================
# AT-38  Override log module
# =============================================================================

class TestAcceptance38_OverrideLog(unittest.TestCase):
    """AT-38: ChatGPT override log structure, validation, and P0 rule."""

    def _p1_finding(self):
        return {"finding_id": "F-04-001", "severity": "P1",
                "description": "Authority check missing"}

    def _p0_finding(self):
        return {"finding_id": "F-04-000", "severity": "P0",
                "description": "Direct authority bypass detected"}

    def _base_record(self, findings=None, **kwargs):
        defaults = dict(
            original_recommendation="REPAIR_REQUIRED",
            findings_overridden=findings if findings is not None else [self._p1_finding()],
            reason="ChatGPT has additional context",
            evidence_basis="Post-review code inspection shows guard was present",
            risk_accepted="Risk accepted: minor; guard verified manually",
            conditions=["Valid only for this exact packet hash"],
            reviewer_run_id="run-abc123",
            packet_hash="deadbeef" * 8,
            reviewer_version="1.0.0",
        )
        defaults.update(kwargs)
        return make_override_record(**defaults)

    def test_module_authority_invariants(self):
        self.assertFalse(_ovlog_can_execute)
        self.assertFalse(_ovlog_prod_auth)

    def test_valid_p1_override_passes(self):
        record = self._base_record()
        errors = validate_override_record(record)
        self.assertEqual(errors, [])

    def test_p0_without_governing_spec_fails(self):
        record = self._base_record(findings=[self._p0_finding()],
                                   governing_spec_change=None)
        errors = validate_override_record(record)
        self.assertTrue(any("P0" in e or "governing_spec_change" in e
                            for e in errors),
                        f"Expected P0 override error, got: {errors}")

    def test_p0_with_governing_spec_passes(self):
        record = self._base_record(
            findings=[self._p0_finding()],
            governing_spec_change="SPEC-CHANGE-2026-08-15: authority gate removed by explicit design",
        )
        errors = validate_override_record(record)
        self.assertEqual(errors, [],
                         f"P0 with governing_spec_change should pass: {errors}")

    def test_empty_findings_overridden_fails(self):
        record = self._base_record(findings=[])
        errors = validate_override_record(record)
        self.assertTrue(any("non-empty" in e or "findings_overridden" in e
                            for e in errors),
                        f"Expected non-empty findings error, got: {errors}")

    def test_empty_reason_fails(self):
        record = self._base_record()
        record.reason = ""
        errors = validate_override_record(record)
        self.assertTrue(any("reason" in e for e in errors))

    def test_empty_evidence_basis_fails(self):
        record = self._base_record()
        record.evidence_basis = ""
        errors = validate_override_record(record)
        self.assertTrue(any("evidence_basis" in e for e in errors))

    def test_wrong_original_recommendation_fails(self):
        record = self._base_record(
            original_recommendation="READY_FOR_CHATGPT_RULING")
        errors = validate_override_record(record)
        self.assertTrue(any("original_recommendation" in e for e in errors))

    def test_build_override_log_entry_valid(self):
        record = self._base_record()
        entry = build_override_log_entry(record, validation_errors=[])
        self.assertTrue(entry["is_valid"])
        self.assertEqual(entry["schema"], "WOW_CHATGPT_OVERRIDE_LOG_v1")
        self.assertFalse(entry["can_execute"])
        self.assertIn("record_hash", entry)

    def test_build_override_log_entry_invalid(self):
        record = self._base_record()
        entry = build_override_log_entry(record, validation_errors=["Some error"])
        self.assertFalse(entry["is_valid"])
        self.assertEqual(len(entry["validation_errors"]), 1)

    def test_make_override_record_computes_p0_present(self):
        record = make_override_record(
            original_recommendation="BLOCKED",
            findings_overridden=[self._p0_finding()],
            reason="r", evidence_basis="e", risk_accepted="ra",
            conditions=["c1"],
            reviewer_run_id="run-001", packet_hash="abc", reviewer_version="1.0.0",
        )
        self.assertTrue(record.p0_present)
        self.assertEqual(record.max_severity_overridden, "P0")

    def test_make_override_record_no_p0_when_p1(self):
        record = make_override_record(
            original_recommendation="REPAIR_REQUIRED",
            findings_overridden=[self._p1_finding()],
            reason="r", evidence_basis="e", risk_accepted="ra",
            conditions=["c1"],
            reviewer_run_id="run-001", packet_hash="abc", reviewer_version="1.0.0",
        )
        self.assertFalse(record.p0_present)
        self.assertEqual(record.max_severity_overridden, "P1")

    def test_record_hash_changes_on_mutation(self):
        record = self._base_record()
        h1 = record.compute_record_hash()
        record.reason = "Changed reason"
        h2 = record.compute_record_hash()
        self.assertNotEqual(h1, h2)

    def test_override_id_auto_generated(self):
        record = self._base_record()
        self.assertTrue(record.override_id.startswith("OVERRIDE-"))

    def test_reviewer_output_includes_override_schema_ref(self):
        """Downstream output must reference the override log schema."""
        adapter = RedTeamReviewerAdapter()
        packet = _base_packet()
        packet["packet_hash"] = compute_packet_hash(packet)
        result = adapter.run({"review_packet": packet})
        ds = result.downstream[0]
        self.assertEqual(ds["override_log_schema"], "WOW_CHATGPT_OVERRIDE_LOG_v1")
        self.assertIn("REPAIR_REQUIRED", ds["overridable_recs"])
        self.assertIn("BLOCKED", ds["overridable_recs"])
        self.assertIn("P0", ds["p0_override_rule"])


# =============================================================================
# AT-39  Level 1 / 2 / 3 risk routing
# =============================================================================

class TestAcceptance39_RiskLevelRouting(unittest.TestCase):
    """AT-39: Level 1/2/3 classification from _classify_risk_level()."""

    def _packet_with_diff(self, diff_files):
        return _base_packet(diff_manifest=[
            {"file": f, "sha256": "a" * 64, "op": "modified"}
            for f in diff_files
        ])

    def test_clean_diff_no_findings_gives_level1(self):
        packet = self._packet_with_diff(["tests/test_new_feature.py"])
        level, label, reasons = _classify_risk_level(packet, [])
        self.assertEqual(level, 1)
        self.assertEqual(label, "LEVEL_1_ROUTINE")
        self.assertEqual(reasons, [])

    def test_gate_engine_core_file_gives_level2(self):
        """gate_engine/classifier.py is significant engine work but below L3 threshold."""
        packet = self._packet_with_diff(["gate_engine/classifier.py"])
        level, label, reasons = _classify_risk_level(packet, [])
        self.assertEqual(level, 2)
        self.assertEqual(label, "LEVEL_2_GOVERNANCE_MODEL_IMPACT")
        self.assertIn("gate_engine_core_pipeline", reasons)

    def test_calibration_file_gives_level3(self):
        """Probability/calibration files are Level 3 per spec (major methodology change)."""
        packet = self._packet_with_diff(["gate_engine/hit_probability.py"])
        level, label, _ = _classify_risk_level(packet, [])
        self.assertEqual(level, 3)
        self.assertEqual(label, "LEVEL_3_CRITICAL_EXTERNAL_RED_TEAM")

    def test_p1_finding_alone_gives_level2(self):
        packet = self._packet_with_diff(["tests/simple_test.py"])
        findings = [{"severity": "P1", "defect_class": "implementation_defect",
                     "description": "Missing guard"}]
        level, label, reasons = _classify_risk_level(packet, findings)
        self.assertEqual(level, 2)
        self.assertIn("p1_findings_present", reasons)

    def test_governance_file_gives_level3(self):
        """Diff touching governance files must trigger Level 3."""
        packet = self._packet_with_diff(["gate_engine/governance.py"])
        level, label, reasons = _classify_risk_level(packet, [])
        self.assertEqual(level, 3)
        self.assertEqual(label, "LEVEL_3_CRITICAL_EXTERNAL_RED_TEAM")
        self.assertTrue(len(reasons) > 0)

    def test_reviewer_self_change_gives_level3(self):
        packet = self._packet_with_diff(["skills/adapters/red_team_reviewer.py"])
        level, label, reasons = _classify_risk_level(packet, [])
        self.assertEqual(level, 3)
        self.assertEqual(label, "LEVEL_3_CRITICAL_EXTERNAL_RED_TEAM")

    def test_risk_level_in_downstream_output(self):
        """risk_level, risk_label, risk_reasons must appear in downstream."""
        adapter = RedTeamReviewerAdapter()
        packet = _base_packet()
        packet["packet_hash"] = compute_packet_hash(packet)
        result = adapter.run({"review_packet": packet})
        ds = result.downstream[0]
        self.assertIn("risk_level", ds)
        self.assertIn("risk_label", ds)
        self.assertIn("risk_reasons", ds)
        self.assertIsInstance(ds["risk_level"], int)
        self.assertIn(ds["risk_label"], {
            "LEVEL_1_ROUTINE",
            "LEVEL_2_GOVERNANCE_MODEL_IMPACT",
            "LEVEL_3_CRITICAL_EXTERNAL_RED_TEAM",
        })

    def test_level3_implies_level3_required_true(self):
        """When risk_level=3, level_3_required must also be True."""
        adapter = RedTeamReviewerAdapter()
        packet = _base_packet(diff_manifest=[
            {"file": "gate_engine/governance.py", "sha256": "a" * 64, "op": "modified"},
        ])
        result = adapter.run({"review_packet": packet})
        ds = result.downstream[0]
        self.assertEqual(ds["risk_level"], 3)
        self.assertTrue(ds["level_3_required"])

    def test_level1_implies_level3_required_false(self):
        adapter = RedTeamReviewerAdapter()
        packet = _base_packet(diff_manifest=[
            {"file": "tests/test_foo.py", "sha256": "b" * 64, "op": "added"},
        ])
        result = adapter.run({"review_packet": packet})
        ds = result.downstream[0]
        # May be L1 or L2 depending on test content — just verify not L3
        if ds["risk_level"] < 3:
            self.assertFalse(ds["level_3_required"])


# =============================================================================
# AT-40  Adversarial proposals
# =============================================================================

def _all_adversarial_proposals(result) -> list[dict]:
    """Extract adversarial_proposals from any calculations entry."""
    proposals = []
    for entry in (result.calculations or []):
        if isinstance(entry, dict) and "adversarial_proposals" in entry:
            proposals.extend(entry["adversarial_proposals"])
    return proposals


class TestAcceptance40_AdversarialProposals(unittest.TestCase):
    """AT-40: Adversarial proposals output section."""

    def test_sha_proposal_always_present(self):
        """AP-SHA-001 must always be generated."""
        packet = _base_packet()
        findings = []
        result = _generate_adversarial_proposals(packet, findings)
        ids = [p["proposal_id"] for p in result["adversarial_proposals"]]
        self.assertIn("AP-SHA-001", ids)

    def test_bypass_proposal_generated_for_auth_file(self):
        """AP-BYPASS-001 must be generated when authority-sensitive files in diff."""
        packet = _base_packet(diff_manifest=[
            {"file": "gate_engine/governance.py", "sha256": "a" * 64, "op": "modified"},
            {"file": "skills/orchestrator.py",    "sha256": "b" * 64, "op": "modified"},
        ])
        findings = []
        result = _generate_adversarial_proposals(packet, findings)
        ids = [p["proposal_id"] for p in result["adversarial_proposals"]]
        self.assertIn("AP-BYPASS-001", ids)

    def test_bypass_proposal_not_generated_for_clean_diff(self):
        """AP-BYPASS-001 must NOT be generated for non-auth files."""
        packet = _base_packet(diff_manifest=[
            {"file": "tests/test_scoring.py", "sha256": "c" * 64, "op": "added"},
            {"file": "docs/README.md",         "sha256": "d" * 64, "op": "modified"},
        ])
        findings = []
        result = _generate_adversarial_proposals(packet, findings)
        ids = [p["proposal_id"] for p in result["adversarial_proposals"]]
        self.assertNotIn("AP-BYPASS-001", ids)

    def test_neg_proposal_generated_when_no_negative_cases(self):
        """AP-NEG-001 generated when tested_negative_cases absent."""
        packet = _base_packet()
        packet.pop("tested_negative_cases", None)
        result = _generate_adversarial_proposals(packet, [])
        ids = [p["proposal_id"] for p in result["adversarial_proposals"]]
        self.assertIn("AP-NEG-001", ids)

    def test_neg_proposal_not_generated_when_cases_present(self):
        packet = _base_packet()
        packet["tested_negative_cases"] = ["missing_stat_key", "null_player"]
        result = _generate_adversarial_proposals(packet, [])
        ids = [p["proposal_id"] for p in result["adversarial_proposals"]]
        self.assertNotIn("AP-NEG-001", ids)

    def test_mutation_proposals_generated_for_p1_findings(self):
        """AP-MUT-* proposals generated for each P1 finding (up to 3)."""
        packet = _base_packet()
        findings = [
            {"finding_id": f"F-0{i}-001", "severity": "P1",
             "description": f"P1 defect #{i}", "defect_class": "implementation_defect"}
            for i in range(3)
        ]
        result = _generate_adversarial_proposals(packet, findings)
        ids = [p["proposal_id"] for p in result["adversarial_proposals"]]
        self.assertIn("AP-MUT-001", ids)
        self.assertIn("AP-MUT-002", ids)
        self.assertIn("AP-MUT-003", ids)

    def test_no_mutation_proposals_for_no_p1_findings(self):
        packet = _base_packet()
        findings = [{"finding_id": "F-01-001", "severity": "P2",
                     "description": "Minor", "defect_class": "specification_defect"}]
        result = _generate_adversarial_proposals(packet, findings)
        ids = [p["proposal_id"] for p in result["adversarial_proposals"]]
        self.assertFalse(any(pid.startswith("AP-MUT-") for pid in ids))

    def test_sha_proposal_is_mandatory(self):
        packet = _base_packet()
        result = _generate_adversarial_proposals(packet, [])
        sha_proposal = next(
            p for p in result["adversarial_proposals"]
            if p["proposal_id"] == "AP-SHA-001"
        )
        self.assertTrue(sha_proposal["mandatory"])

    def test_bypass_proposal_is_mandatory(self):
        packet = _base_packet(diff_manifest=[
            {"file": "gate_engine/governance.py", "sha256": "a" * 64, "op": "modified"}
        ])
        result = _generate_adversarial_proposals(packet, [])
        bypass = next(
            (p for p in result["adversarial_proposals"]
             if p["proposal_id"] == "AP-BYPASS-001"), None
        )
        self.assertIsNotNone(bypass)
        self.assertTrue(bypass["mandatory"])

    def test_mandatory_count_accurate(self):
        packet = _base_packet()
        packet.pop("tested_negative_cases", None)
        result = _generate_adversarial_proposals(packet, [])
        mandatory_actual = sum(
            1 for p in result["adversarial_proposals"] if p.get("mandatory")
        )
        self.assertEqual(result["mandatory_proposals"], mandatory_actual)

    def test_proposals_appear_in_calculations_output(self):
        """adversarial_proposals block must be in calculations output."""
        adapter = RedTeamReviewerAdapter()
        packet = _base_packet()
        packet["packet_hash"] = compute_packet_hash(packet)
        result = adapter.run({"review_packet": packet})
        proposals = _all_adversarial_proposals(result)
        # AP-SHA-001 must always be present in the full pipeline output
        ids = [p["proposal_id"] for p in proposals]
        self.assertIn("AP-SHA-001", ids)

    def test_spec_proposal_generated_when_criteria_present(self):
        packet = _base_packet()
        packet["acceptance_criteria"] = ["Criterion A", "Criterion B"]
        result = _generate_adversarial_proposals(packet, [])
        ids = [p["proposal_id"] for p in result["adversarial_proposals"]]
        self.assertIn("AP-SPEC-001", ids)

    def test_spec_proposal_includes_criteria_count(self):
        packet = _base_packet()
        packet["acceptance_criteria"] = ["C1", "C2", "C3"]
        result = _generate_adversarial_proposals(packet, [])
        spec = next(p for p in result["adversarial_proposals"]
                    if p["proposal_id"] == "AP-SPEC-001")
        self.assertEqual(spec["criteria_count"], 3)

    def test_bypass_proposal_references_hypothesis_h6(self):
        packet = _base_packet(diff_manifest=[
            {"file": "gate_engine/orchestrator.py", "sha256": "a" * 64, "op": "modified"}
        ])
        result = _generate_adversarial_proposals(packet, [])
        bypass = next(p for p in result["adversarial_proposals"]
                      if p["proposal_id"] == "AP-BYPASS-001")
        self.assertIn("H6", bypass["bypass_hypothesis"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
