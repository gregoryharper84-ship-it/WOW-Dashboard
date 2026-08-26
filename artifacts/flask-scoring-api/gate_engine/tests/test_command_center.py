"""
Acceptance tests — WOW Sports Intelligence Command Center (Phase 1)
PATCH: WOW-CC-PHASE1-2026-08-06

Coverage:
  UNIT     — label constants and ceiling primitives
  INTAKE   — candidate validation and envelope normalization
  ROUTER   — strict single-engine routing
  CEILING  — monotonic ceiling enforcement
  SERVICE  — shared service layer
  KALSHI   — Recovery Mode isolation
  RECON    — row reconciliation rules
  ORCH     — full orchestration pipeline

All tests assert can_execute=False.  No test may assert can_execute=True.
"""
from __future__ import annotations

import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../.."))

import pytest
from gate_engine.command_center.cc_labels import (
    CAN_EXECUTE, DRY_RUN_ONLY, KALSHI_RECOVERY_MODE,
    CEILING_ORDER, ceiling_rank,
    FAMILY_PROP, FAMILY_LLP, FAMILY_KALSHI_SPORTS, FAMILY_KALSHI_WEATHER,
    ALL_FAMILIES,
    CC_INTAKE_INVALID, CC_INTAKE_MISSING_FAMILY,
    CC_INTAKE_MISSING_DATE, CC_INTAKE_MISSING_IDENTITY,
    CC_ROUTING_CONFLICT, CC_ROUTING_UNRESOLVABLE,
    CC_CEILING_ENFORCED, CC_UPSTREAM_BLOCKER_PRESERVED,
    CC_KALSHI_RECOVERY_CAP, CC_KALSHI_CONTAMINATION_BLOCK,
    CC_RECONCILIATION_PASSED, CC_RECONCILIATION_FAILED,
    CC_MISSING_FINAL_LABEL, CC_CAN_EXECUTE_VIOLATION,
    is_reject_label, is_approval_label, is_cc_label,
)
from gate_engine.command_center.ceiling_resolver import (
    resolve_ceiling, apply_ceiling_to_row,
    enforce_batch_ceilings, check_no_upstream_erasure,
)
from gate_engine.command_center.candidate_intake import (
    make_envelope, validate_batch, extract_engine_label,
)
from gate_engine.command_center.market_router import (
    route_candidate, route_batch,
)
from gate_engine.command_center.shared_services import (
    run_slate_integrity, run_cross_platform_exposure,
    run_final_refresh_check, run_exact_line_audit,
    run_all as run_shared_services,
)
from gate_engine.command_center.kalshi_isolation import (
    apply_recovery_mode_caps, check_cross_contamination,
)
from gate_engine.command_center.reconciliation import (
    reconcile_row, reconcile_batch,
)
from gate_engine.command_center.orchestrator import (
    run_intake, run_command_center,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def _prop_raw(
    candidate_id="prop-001",
    slate_date="2026-08-06",
    player="Shohei Ohtani",
    prop_type="Strikeouts",
    line=6.5,
    direction="MORE",
    event_id="CHC@LAD-2026-08-06",
) -> dict:
    return {
        "candidate_id": candidate_id,
        "market_family": FAMILY_PROP,
        "sport": "MLB",
        "player": player,
        "prop_type": prop_type,
        "line": line,
        "direction": direction,
        "slate_date": slate_date,
        "event_id": event_id,
    }


def _llp_raw(
    candidate_id="llp-001",
    slate_date="2026-08-06",
    event_id="CHC@LAD-2026-08-06",
) -> dict:
    return {
        "candidate_id": candidate_id,
        "market_family": FAMILY_LLP,
        "sport": "MLB",
        "event_id": event_id,
        "slate_date": slate_date,
    }


def _kalshi_sports_raw(
    candidate_id="ks-001",
    slate_date="2026-08-06",
    event_id="KXMLB-CHC-WIN-2026-08-06",
) -> dict:
    return {
        "candidate_id": candidate_id,
        "market_family": FAMILY_KALSHI_SPORTS,
        "sport": "KALSHI",
        "event_id": event_id,
        "slate_date": slate_date,
        "category": "sports_winner",
    }


def _kalshi_weather_raw(
    candidate_id="kw-001",
    slate_date="2026-08-06",
    event_id="KXNYC-HIGH-2026-08-06",
) -> dict:
    return {
        "candidate_id": candidate_id,
        "market_family": FAMILY_KALSHI_WEATHER,
        "sport": "KALSHI",
        "event_id": event_id,
        "slate_date": slate_date,
        "category": "weather",
        "city": "NYC",
    }


def _make_routed_env(raw: dict) -> dict:
    """Make a fully routed envelope (intake + routing applied)."""
    env = make_envelope(raw)
    route_candidate(env)
    return env


# ---------------------------------------------------------------------------
# UNIT tests — label constants and ceiling primitives
# ---------------------------------------------------------------------------

class TestUnit:

    def test_unit_001_can_execute_is_false(self):
        """UNIT-001: CAN_EXECUTE module constant is always False."""
        assert CAN_EXECUTE is False

    def test_unit_002_dry_run_only_is_true(self):
        """UNIT-002: DRY_RUN_ONLY is always True."""
        assert DRY_RUN_ONLY is True

    def test_unit_003_kalshi_recovery_mode_active(self):
        """UNIT-003: KALSHI_RECOVERY_MODE is 'ACTIVE'."""
        assert KALSHI_RECOVERY_MODE == "ACTIVE"

    def test_unit_004_ceiling_order_is_ordered(self):
        """UNIT-004: ceiling_rank is monotonically increasing with index."""
        for i, label in enumerate(CEILING_ORDER):
            assert ceiling_rank(label) == i, (
                f"UNIT-004: {label!r} at index {i} has rank {ceiling_rank(label)}"
            )

    def test_unit_005_final_approved_less_restrictive_than_reject(self):
        """UNIT-005: FINAL_APPROVED is less restrictive than any REJECT label."""
        assert ceiling_rank("FINAL_APPROVED") < ceiling_rank("REJECT_NO_EDGE"), (
            "UNIT-005: FINAL_APPROVED must rank below REJECT labels"
        )

    def test_unit_006_resolve_ceiling_takes_more_restrictive(self):
        """UNIT-006: resolve_ceiling returns the more restrictive label."""
        result = resolve_ceiling("FINAL_APPROVED", "REJECT_NO_EDGE")
        assert result == "REJECT_NO_EDGE"

    def test_unit_007_resolve_ceiling_upstream_preserved(self):
        """UNIT-007: resolve_ceiling preserves upstream when candidate is less restrictive."""
        result = resolve_ceiling("REJECT_NO_EDGE", "FINAL_APPROVED")
        assert result == "REJECT_NO_EDGE", (
            "UNIT-007: upstream blocker must not be downgraded by less-restrictive candidate"
        )

    def test_unit_008_resolve_ceiling_none_handling(self):
        """UNIT-008: resolve_ceiling handles None correctly."""
        assert resolve_ceiling(None, "REJECT_NO_EDGE") == "REJECT_NO_EDGE"
        assert resolve_ceiling("REJECT_NO_EDGE", None) == "REJECT_NO_EDGE"
        assert resolve_ceiling(None, None) is None

    def test_unit_009_cc_labels_are_namespaced(self):
        """UNIT-009: All CC-specific labels start with 'CC:'."""
        cc_labels = [
            CC_INTAKE_INVALID, CC_INTAKE_MISSING_FAMILY,
            CC_ROUTING_CONFLICT, CC_ROUTING_UNRESOLVABLE,
            CC_CEILING_ENFORCED, CC_UPSTREAM_BLOCKER_PRESERVED,
            CC_KALSHI_RECOVERY_CAP, CC_KALSHI_CONTAMINATION_BLOCK,
            CC_RECONCILIATION_PASSED, CC_RECONCILIATION_FAILED,
        ]
        for label in cc_labels:
            assert is_cc_label(label), f"UNIT-009: {label!r} should start with 'CC:'"

    def test_unit_010_is_reject_label(self):
        """UNIT-010: is_reject_label correctly identifies reject-tier labels."""
        assert is_reject_label("REJECT_NO_EDGE") is True
        assert is_reject_label("REJECT_BAD_STRUCTURE") is True
        assert is_reject_label("FINAL_APPROVED") is False
        assert is_reject_label("RESEARCH_INTEREST") is False
        assert is_reject_label(None) is False

    def test_unit_011_all_families_complete(self):
        """UNIT-011: ALL_FAMILIES contains all four engine families."""
        assert FAMILY_PROP in ALL_FAMILIES
        assert FAMILY_LLP in ALL_FAMILIES
        assert FAMILY_KALSHI_SPORTS in ALL_FAMILIES
        assert FAMILY_KALSHI_WEATHER in ALL_FAMILIES
        assert len(ALL_FAMILIES) == 4


# ---------------------------------------------------------------------------
# INTAKE tests — candidate validation
# ---------------------------------------------------------------------------

class TestIntake:

    def test_intake_001_valid_prop_candidate(self):
        """INTAKE-001: Valid PROP candidate passes intake."""
        env = make_envelope(_prop_raw())
        assert env["intake_valid"] is True
        assert env["can_execute"] is False
        assert env["market_family"] == FAMILY_PROP
        assert CC_INTAKE_INVALID not in env["cc_blockers"]

    def test_intake_002_missing_market_family(self):
        """INTAKE-002: Missing market_family → CC:INTAKE_MISSING_FAMILY blocker."""
        raw = _prop_raw()
        raw.pop("market_family")
        env = make_envelope(raw)
        assert env["intake_valid"] is False
        assert CC_INTAKE_MISSING_FAMILY in env["cc_blockers"]
        assert CC_INTAKE_INVALID in env["cc_blockers"]

    def test_intake_003_missing_slate_date(self):
        """INTAKE-003: Missing slate_date → CC:INTAKE_MISSING_DATE blocker."""
        raw = _prop_raw()
        raw.pop("slate_date")
        env = make_envelope(raw)
        assert env["intake_valid"] is False
        assert CC_INTAKE_MISSING_DATE in env["cc_blockers"]

    def test_intake_004_missing_identity(self):
        """INTAKE-004: Missing both player and event_id → CC:INTAKE_MISSING_IDENTITY."""
        raw = _prop_raw()
        raw.pop("player")
        raw.pop("event_id")
        env = make_envelope(raw)
        assert env["intake_valid"] is False
        assert CC_INTAKE_MISSING_IDENTITY in env["cc_blockers"]

    def test_intake_005_batch_validate_splits_valid_invalid(self):
        """INTAKE-005: validate_batch returns (valid, invalid) correctly."""
        raws = [
            _prop_raw("valid-1"),
            {**_prop_raw("invalid-1"), "market_family": "NONEXISTENT"},
            _llp_raw("valid-2"),
        ]
        valid, invalid = validate_batch(raws)
        assert len(valid) == 2
        assert len(invalid) == 1
        assert invalid[0]["candidate_id"] == "invalid-1"

    def test_intake_006_can_execute_always_false_in_envelope(self):
        """INTAKE-006: can_execute=False on every envelope regardless of validity."""
        for raw in [_prop_raw(), _llp_raw(), _kalshi_sports_raw()]:
            env = make_envelope(raw)
            assert env["can_execute"] is False

    def test_intake_007_all_families_accepted(self):
        """INTAKE-007: All four market families pass intake validation."""
        for raw in [_prop_raw(), _llp_raw(), _kalshi_sports_raw(), _kalshi_weather_raw()]:
            env = make_envelope(raw)
            assert env["intake_valid"] is True
            assert env["can_execute"] is False


# ---------------------------------------------------------------------------
# ROUTER tests — strict single-engine routing
# ---------------------------------------------------------------------------

class TestRouter:

    def test_router_001_prop_assigned_to_prop(self):
        """ROUTER-001: PROP family candidate routes to PROP."""
        env = make_envelope(_prop_raw())
        result = route_candidate(env)
        assert result["routing_status"] == "ASSIGNED"
        assert result["assigned_family"] == FAMILY_PROP
        assert env["assigned_family"] == FAMILY_PROP

    def test_router_002_kalshi_sports_assigned(self):
        """ROUTER-002: KALSHI_SPORTS candidate routes to KALSHI_SPORTS."""
        env = make_envelope(_kalshi_sports_raw())
        result = route_candidate(env)
        assert result["routing_status"] == "ASSIGNED"
        assert result["assigned_family"] == FAMILY_KALSHI_SPORTS

    def test_router_003_kalshi_weather_assigned(self):
        """ROUTER-003: KALSHI_WEATHER candidate routes to KALSHI_WEATHER."""
        env = make_envelope(_kalshi_weather_raw())
        result = route_candidate(env)
        assert result["routing_status"] == "ASSIGNED"
        assert result["assigned_family"] == FAMILY_KALSHI_WEATHER

    def test_router_004_platform_mismatch_causes_conflict(self):
        """ROUTER-004: KALSHI platform declared as PROP → routing conflict."""
        raw = _prop_raw()
        raw["platform"] = "KALSHI"
        env = make_envelope(raw)
        result = route_candidate(env)
        assert result["routing_status"] == "CONFLICT"
        assert CC_ROUTING_CONFLICT in env["cc_blockers"]

    def test_router_005_no_family_is_unresolvable(self):
        """ROUTER-005: No valid family → ROUTING_UNRESOLVABLE."""
        env = make_envelope(_prop_raw())
        env["market_family"] = None   # clear the family post-intake
        result = route_candidate(env)
        assert result["routing_status"] == "UNRESOLVABLE"
        assert CC_ROUTING_UNRESOLVABLE in env["cc_blockers"]

    def test_router_006_batch_groups_correctly(self):
        """ROUTER-006: route_batch groups candidates by family."""
        envs = [
            make_envelope(_prop_raw("p1")),
            make_envelope(_llp_raw("l1")),
            make_envelope(_kalshi_sports_raw("ks1")),
            make_envelope(_kalshi_weather_raw("kw1")),
        ]
        report = route_batch(envs)
        assert report["routing_summary"][FAMILY_PROP] == 1
        assert report["routing_summary"][FAMILY_LLP] == 1
        assert report["routing_summary"][FAMILY_KALSHI_SPORTS] == 1
        assert report["routing_summary"][FAMILY_KALSHI_WEATHER] == 1
        assert report["total_routed"] == 4
        assert report["can_execute"] is False

    def test_router_007_exactly_one_family_per_candidate(self):
        """ROUTER-007: Each candidate appears in exactly one family bucket."""
        envs = [make_envelope(r) for r in [
            _prop_raw("p1"), _llp_raw("l1"), _kalshi_sports_raw("ks1"),
        ]]
        report = route_batch(envs)
        all_assigned = []
        for family_list in report["by_family"].values():
            for env in family_list:
                all_assigned.append(env["candidate_id"])
        # No duplicates
        assert len(all_assigned) == len(set(all_assigned)), (
            "ROUTER-007: A candidate appeared in more than one family bucket"
        )


# ---------------------------------------------------------------------------
# CEILING tests — monotonic enforcement
# ---------------------------------------------------------------------------

class TestCeiling:

    def test_ceiling_001_only_moves_to_more_restrictive(self):
        """CEILING-001: apply_ceiling_to_row only changes ceiling to more restrictive label."""
        row = {"cc_ceiling": "RESEARCH_INTEREST", "cc_blockers": []}
        changed = apply_ceiling_to_row(row, "REJECT_NO_EDGE", source="test")
        assert changed is True
        assert row["cc_ceiling"] == "REJECT_NO_EDGE"

    def test_ceiling_002_upstream_blocker_preserved(self):
        """CEILING-002: Less-restrictive proposed label is rejected; upstream preserved."""
        row = {"cc_ceiling": "REJECT_NO_EDGE", "cc_blockers": []}
        changed = apply_ceiling_to_row(row, "FINAL_APPROVED", source="test")
        assert changed is False
        assert row["cc_ceiling"] == "REJECT_NO_EDGE", (
            "CEILING-002: Upstream REJECT blocker was erroneously upgraded to FINAL_APPROVED"
        )
        assert any(CC_UPSTREAM_BLOCKER_PRESERVED in b for b in row["cc_blockers"]), (
            "CEILING-002: CC:UPSTREAM_BLOCKER_PRESERVED not stamped"
        )

    def test_ceiling_003_no_ceiling_then_first_label_accepted(self):
        """CEILING-003: First ceiling label is always accepted."""
        row = {"cc_ceiling": None, "cc_blockers": []}
        changed = apply_ceiling_to_row(row, "RESEARCH_INTEREST", source="test")
        assert changed is True
        assert row["cc_ceiling"] == "RESEARCH_INTEREST"

    def test_ceiling_004_enforce_batch_stamps_can_execute_false(self):
        """CEILING-004: enforce_batch_ceilings stamps can_execute=False on every row."""
        rows = [
            {"engine_label": "FINAL_APPROVED", "cc_ceiling": None, "cc_blockers": []},
            {"engine_label": "REJECT_NO_EDGE", "cc_ceiling": None, "cc_blockers": []},
            {"engine_label": None, "cc_ceiling": "RESEARCH_INTEREST", "cc_blockers": []},
        ]
        enforce_batch_ceilings(rows)
        for row in rows:
            assert row["can_execute"] is False, (
                f"CEILING-004: can_execute not False after enforcement: {row}"
            )

    def test_ceiling_005_final_label_resolves_most_restrictive(self):
        """CEILING-005: final_label is the most restrictive of engine_label and cc_ceiling."""
        row = {
            "engine_label": "RESEARCH_INTEREST",
            "cc_ceiling":   "REJECT_NO_EDGE",      # more restrictive
            "cc_blockers":  [],
        }
        enforce_batch_ceilings([row])
        assert row["final_label"] == "REJECT_NO_EDGE", (
            f"CEILING-005: expected REJECT_NO_EDGE, got {row['final_label']!r}"
        )

    def test_ceiling_006_upstream_erasure_check_catches_violation(self):
        """CEILING-006: check_no_upstream_erasure detects monotonic violations."""
        rows = [
            {
                "candidate_id": "viol-001",
                "final_label":  "FINAL_APPROVED",    # less restrictive
                "cc_ceiling":   "REJECT_NO_EDGE",    # more restrictive (set upstream)
            }
        ]
        violations = check_no_upstream_erasure(rows)
        assert len(violations) == 1
        assert violations[0]["candidate_id"] == "viol-001"
        assert violations[0]["violation"] == "FINAL_LABEL_LESS_RESTRICTIVE_THAN_CC_CEILING"

    def test_ceiling_007_no_violation_when_compliant(self):
        """CEILING-007: No erasure violations when final_label >= cc_ceiling."""
        rows = [
            {"candidate_id": "ok-001", "final_label": "REJECT_NO_EDGE",
             "cc_ceiling": "RESEARCH_INTEREST"},
            {"candidate_id": "ok-002", "final_label": "REJECT_NO_EDGE",
             "cc_ceiling": "REJECT_NO_EDGE"},
        ]
        violations = check_no_upstream_erasure(rows)
        assert violations == [], f"CEILING-007: Unexpected violations: {violations}"


# ---------------------------------------------------------------------------
# SERVICE tests — shared services
# ---------------------------------------------------------------------------

class TestSharedServices:

    def test_service_001_slate_integrity_passes_matching_date(self):
        """SERVICE-001: Slate integrity passes when all candidates match target_date."""
        envs = [make_envelope(_prop_raw(slate_date="2026-08-06"))]
        report = run_slate_integrity(envs, "2026-08-06")
        assert report["status"] == "PASSED"
        assert envs[0]["slate_integrity_ok"] is True

    def test_service_002_slate_integrity_fails_mismatched_date(self):
        """SERVICE-002: Slate integrity fails and adds blocker when date mismatches."""
        env = make_envelope(_prop_raw(slate_date="2026-08-05"))  # wrong date
        report = run_slate_integrity([env], "2026-08-06")
        assert report["status"] == "FAILED"
        assert env["slate_integrity_ok"] is False
        assert any("SLATE_INTEGRITY_FAILED" in b for b in env["cc_blockers"])

    def test_service_003_cross_platform_exposure_detects_prop_duplicate(self):
        """SERVICE-003: Cross-platform exposure detects same player+prop+direction duplicate."""
        env1 = make_envelope(_prop_raw("p1"))
        env2 = make_envelope(_prop_raw("p2"))  # same player/prop/direction
        report = run_cross_platform_exposure([env1, env2])
        assert report["status"] == "FAILED"
        assert len(report["conflicts"]) == 1
        assert env2["exposure_conflict"] is True

    def test_service_004_cross_platform_no_conflict_different_players(self):
        """SERVICE-004: No exposure conflict for different players."""
        env1 = make_envelope(_prop_raw("p1", player="Player A"))
        env2 = make_envelope(_prop_raw("p2", player="Player B"))
        report = run_cross_platform_exposure([env1, env2])
        assert report["status"] == "PASSED"

    def test_service_005_final_refresh_flags_stale_timestamp(self):
        """SERVICE-005: Final refresh flags candidate with old timestamp."""
        env = make_envelope(_prop_raw())
        # Inject a very old timestamp in engine_result
        env["engine_result"] = {"checked_at": "2020-01-01T00:00:00Z"}
        report = run_final_refresh_check([env], freshness_window_minutes=30)
        assert report["status"] == "FAILED"
        assert env["final_refresh_ok"] is False
        assert any("FINAL_REFRESH_REQUIRED" in b for b in env["cc_blockers"])

    def test_service_006_exact_line_audit_catches_mismatch(self):
        """SERVICE-006: Exact-line audit catches significant line mismatch."""
        env = make_envelope(_prop_raw(line=6.5))
        env["engine_result"] = {"line": 8.0}   # > 0.5 difference
        report = run_exact_line_audit([env])
        assert report["status"] == "FAILED"
        assert env["exact_line_audit_ok"] is False
        assert any("EXACT_LINE_MISMATCH" in b for b in env["cc_blockers"])

    def test_service_007_exact_line_passes_within_tolerance(self):
        """SERVICE-007: Exact-line audit passes when within 0.5 tolerance."""
        env = make_envelope(_prop_raw(line=6.5))
        env["engine_result"] = {"line": 6.5}
        report = run_exact_line_audit([env])
        assert report["status"] == "PASSED"
        assert env["exact_line_audit_ok"] is True

    def test_service_008_shared_services_can_execute_always_false(self):
        """SERVICE-008: can_execute=False in all shared service reports."""
        envs = [make_envelope(_prop_raw())]
        report = run_shared_services(envs, "2026-08-06")
        assert report["can_execute"] is False
        for sub_report in report["reports"].values():
            assert sub_report.get("can_execute") is False


# ---------------------------------------------------------------------------
# KALSHI tests — Recovery Mode isolation
# ---------------------------------------------------------------------------

class TestKalshiIsolation:

    def test_kalshi_001_recovery_mode_constant_active(self):
        """KALSHI-001: KALSHI_RECOVERY_MODE is 'ACTIVE'."""
        from gate_engine.command_center.kalshi_isolation import KALSHI_RECOVERY_MODE
        assert KALSHI_RECOVERY_MODE == "ACTIVE"

    def test_kalshi_002_max_2_total_cap_enforced(self):
        """KALSHI-002: Recovery Mode caps at max 2 Kalshi candidates."""
        envs = [make_envelope(_kalshi_sports_raw(f"ks-{i}")) for i in range(4)]
        for env in envs:
            route_candidate(env)
            env["engine_result"] = {
                "research_eligible": True,
                "net_edge_lower_bound": 0.05,
                "calibration_strength": 0.7,
                "model_uncertainty": 0.2,
                "price_age_minutes": 10.0,
                "calibrated_prob_lower_bound": 0.65,
                "settlement_clarity_grade": "B",
                "spread_cents": 5.0,
                "exposure_overlap": False,
                "is_multi_leg": False,
            }
        report = apply_recovery_mode_caps(envs)
        assert report["survivors"] <= 2, (
            f"KALSHI-002: Expected ≤2 survivors, got {report['survivors']}"
        )
        assert report["can_execute"] is False

    def test_kalshi_003_contamination_non_kalshi_has_kalshi_label(self):
        """KALSHI-003: Non-Kalshi candidate with Kalshi label → contamination block."""
        env = make_envelope(_prop_raw())
        env["assigned_family"] = FAMILY_PROP
        env["engine_label"] = "KALSHI_SCOUT"   # Kalshi label on Prop candidate
        violations = check_cross_contamination([env])
        assert len(violations) == 1
        assert any("CC:KALSHI" in b for b in env["cc_blockers"])

    def test_kalshi_004_kalshi_candidate_with_approval_label_is_contamination(self):
        """KALSHI-004: Kalshi candidate with FINAL_APPROVED label → contamination."""
        env = make_envelope(_kalshi_sports_raw())
        env["assigned_family"] = FAMILY_KALSHI_SPORTS
        env["engine_label"] = "FINAL_APPROVED"  # Non-Kalshi label on Kalshi candidate
        violations = check_cross_contamination([env])
        assert len(violations) == 1

    def test_kalshi_005_no_contamination_clean_candidates(self):
        """KALSHI-005: Clean candidates produce no contamination violations."""
        prop_env = make_envelope(_prop_raw())
        prop_env["assigned_family"] = FAMILY_PROP
        prop_env["engine_label"] = "RESEARCH_INTEREST"

        kalshi_env = make_envelope(_kalshi_sports_raw())
        kalshi_env["assigned_family"] = FAMILY_KALSHI_SPORTS
        kalshi_env["engine_label"] = "KALSHI_SCOUT"

        violations = check_cross_contamination([prop_env, kalshi_env])
        assert violations == []


# ---------------------------------------------------------------------------
# RECON tests — row reconciliation rules
# ---------------------------------------------------------------------------

class TestReconciliation:

    def _base_row(self, candidate_id="recon-001", family=FAMILY_PROP) -> dict:
        env = make_envelope(_prop_raw(candidate_id))
        env["assigned_family"] = family
        env["engine_result"]   = {"terminal_labels": {}, "final_card": [], "can_execute": False}
        env["engine_label"]    = "RESEARCH_INTEREST"
        env["final_label"]     = "RESEARCH_INTEREST"
        env["can_execute"]     = False
        env["kalshi_recovery_caps_applied"] = False
        return env

    def test_recon_001_missing_final_label_caught(self):
        """RECON-001: R-01 — missing final_label triggers reconciliation failure."""
        row = self._base_row()
        row["final_label"] = None
        result = reconcile_row(row)
        assert row["reconciliation_status"] == CC_RECONCILIATION_FAILED
        assert CC_MISSING_FINAL_LABEL in row["cc_blockers"]

    def test_recon_002_can_execute_violation_caught(self):
        """RECON-002: R-02 — can_execute=True triggers reconciliation failure and correction."""
        row = self._base_row()
        row["can_execute"] = True   # violation
        reconcile_row(row)
        assert row["reconciliation_status"] == CC_RECONCILIATION_FAILED
        assert CC_CAN_EXECUTE_VIOLATION in row["cc_blockers"]
        assert row["can_execute"] is False   # corrected in place

    def test_recon_003_monotonic_violation_caught(self):
        """RECON-003: R-03 — final_label less restrictive than cc_ceiling → failure."""
        row = self._base_row()
        row["cc_ceiling"]  = "REJECT_NO_EDGE"    # more restrictive
        row["final_label"] = "FINAL_APPROVED"    # less restrictive — violation
        reconcile_row(row)
        assert row["reconciliation_status"] == CC_RECONCILIATION_FAILED

    def test_recon_004_kalshi_caps_not_applied_caught(self):
        """RECON-004: R-05 — Kalshi candidate without recovery caps → failure."""
        row = self._base_row(family=FAMILY_KALSHI_SPORTS)
        row["market_family"] = FAMILY_KALSHI_SPORTS
        row["kalshi_recovery_caps_applied"] = False   # not applied
        reconcile_row(row)
        assert row["reconciliation_status"] == CC_RECONCILIATION_FAILED

    def test_recon_005_valid_row_passes_all_checks(self):
        """RECON-005: Fully valid row passes reconciliation."""
        row = self._base_row()
        row["can_execute"]  = False
        row["final_label"]  = "RESEARCH_INTEREST"
        row["cc_ceiling"]   = "RESEARCH_INTEREST"
        row["intake_valid"] = True
        reconcile_row(row)
        assert row["reconciliation_status"] == CC_RECONCILIATION_PASSED

    def test_recon_006_batch_reconcile_summary(self):
        """RECON-006: reconcile_batch returns correct pass/fail counts."""
        good = self._base_row("good-001")
        good["final_label"] = "RESEARCH_INTEREST"
        good["cc_ceiling"]  = "RESEARCH_INTEREST"
        good["intake_valid"] = True

        bad = self._base_row("bad-001")
        bad["final_label"] = None   # triggers R-01

        report = reconcile_batch([good, bad])
        assert report["total"] == 2
        assert report["passed"] == 1
        assert report["failed"] == 1
        assert report["all_passed"] is False
        assert report["can_execute"] is False


# ---------------------------------------------------------------------------
# ORCH tests — full orchestration pipeline
# ---------------------------------------------------------------------------

class TestOrchestration:

    def test_orch_001_intake_run_returns_routing_manifest(self):
        """ORCH-001: run_intake returns a routing manifest with correct structure."""
        raws = [_prop_raw(), _llp_raw(), _kalshi_sports_raw()]
        manifest = run_intake(raws, session_id="sess-001",
                              run_id="run-001", target_date="2026-08-06")
        assert manifest["phase"] == "A_INTAKE_ROUTING"
        assert manifest["can_execute"] is False
        assert manifest["dry_run_only"] is True
        assert manifest["kalshi_recovery_mode"] == "ACTIVE"
        assert manifest["intake"]["total_received"] == 3
        assert manifest["routing"]["total_routed"] == 3

    def test_orch_002_full_run_can_execute_false_on_all_candidates(self):
        """ORCH-002: Every candidate in the full run output has can_execute=False."""
        raws = [_prop_raw("p1"), _llp_raw("l1"), _kalshi_sports_raw("ks1")]
        result = run_command_center(raws, target_date="2026-08-06")
        assert result["can_execute"] is False
        for cand in result["candidates"]:
            assert cand["can_execute"] is False, (
                f"ORCH-002: candidate {cand.get('candidate_id')!r} "
                f"has can_execute={cand.get('can_execute')!r}"
            )

    def test_orch_003_empty_candidate_list_valid_output(self):
        """ORCH-003: Empty candidate list returns valid output without error."""
        result = run_command_center([], target_date="2026-08-06")
        assert result["can_execute"] is False
        assert result["candidates"] == []
        assert result["summary"]["total_candidates"] == 0

    def test_orch_004_invalid_candidate_included_not_dropped(self):
        """ORCH-004: Invalid candidates are included in output (not silently dropped)."""
        raws = [
            _prop_raw("valid-001"),
            {**_prop_raw("invalid-001"), "market_family": "BADVALUE"},
        ]
        result = run_command_center(raws, target_date="2026-08-06")
        all_ids = [c.get("candidate_id") for c in result["candidates"]]
        assert "valid-001" in all_ids, "ORCH-004: Valid candidate missing from output"
        assert "invalid-001" in all_ids, "ORCH-004: Invalid candidate was silently dropped"

    def test_orch_005_upstream_blocker_not_erased_by_engine_result(self):
        """ORCH-005: Engine result cannot erase a CC blocker set at intake."""
        raw = _prop_raw("orch-001")
        # Simulate an intake failure (missing date)
        raw.pop("slate_date")
        # Provide an engine result claiming FINAL_APPROVED
        engine_results = {"orch-001": {
            "terminal_labels": {"row-1": "FINAL_APPROVED"},
            "final_card": [],
            "can_execute": False,
        }}
        result = run_command_center([raw], engine_results=engine_results,
                                    target_date="2026-08-06")
        cand = next(c for c in result["candidates"] if c["candidate_id"] == "orch-001")
        # The CC intake blocker must still be present
        assert any("INTAKE" in b for b in cand["cc_blockers"]), (
            "ORCH-005: CC intake blocker was erased by engine result"
        )
        # Final label must not be FINAL_APPROVED (it's overridden by CC ceiling)
        assert cand["final_label"] != "FINAL_APPROVED", (
            "ORCH-005: Engine's FINAL_APPROVED was not overridden by upstream CC ceiling"
        )

    def test_orch_006_governance_invariants_in_output(self):
        """ORCH-006: Output always carries the three governance invariants."""
        result = run_command_center([_prop_raw()], target_date="2026-08-06")
        assert result["can_execute"]          is False
        assert result["dry_run_only"]         is True
        assert result["kalshi_recovery_mode"] == "ACTIVE"

    def test_orch_007_kalshi_recovery_mode_isolated(self):
        """ORCH-007: Kalshi candidates go through recovery mode isolation."""
        raws = [_kalshi_sports_raw(f"ks-{i}") for i in range(3)]
        result = run_command_center(raws, target_date="2026-08-06")
        kalshi_report = result["kalshi_report"]
        # Report must exist and show recovery mode was applied or candidates processed
        assert "recovery_mode" in kalshi_report or kalshi_report.get("status") in (
            "APPLIED", "APPLIED_FALLBACK"
        )
        assert result["can_execute"] is False

    def test_orch_008_routing_report_in_output(self):
        """ORCH-008: Routing report is present in full run output."""
        raws = [_prop_raw(), _llp_raw()]
        result = run_command_center(raws, target_date="2026-08-06")
        rr = result["routing_report"]
        assert "total_routed" in rr
        assert "routing_summary" in rr
        assert rr["can_execute"] is False

    def test_orch_009_reconciliation_report_in_output(self):
        """ORCH-009: Reconciliation report is present in full run output."""
        result = run_command_center([_prop_raw()], target_date="2026-08-06")
        assert "reconciliation_report" in result
        assert "total" in result["reconciliation_report"]

    def test_orch_010_summary_by_family_counts(self):
        """ORCH-010: Summary by_family counts match routing."""
        raws = [_prop_raw("p1"), _prop_raw("p2"), _llp_raw("l1")]
        result = run_command_center(raws, target_date="2026-08-06")
        summary = result["summary"]
        assert summary["by_family"][FAMILY_PROP] == 2
        assert summary["by_family"][FAMILY_LLP] == 1
        assert summary["can_execute"] is False
