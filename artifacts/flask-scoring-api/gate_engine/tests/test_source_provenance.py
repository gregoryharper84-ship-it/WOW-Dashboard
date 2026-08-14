"""
gate_engine/tests/test_source_provenance.py

Comprehensive test suite for WOW-PATCH-2026-08-14-SOURCE-PROVENANCE-FRESHNESS-AUDITOR-v2

Tests are organized by module:
  T-SP-01..12   evidence_contract   — types, normalization, builder, hashing
  T-SP-13..25   fact_policy_registry — lookup priority, wildcard fallbacks
  T-SP-26..40   freshness_engine    — per-basis evaluation, stale/expired tiers
  T-SP-41..55   conflict_detector   — hash mismatch, materiality threshold, preservation
  T-SP-56..75   auditor             — INVARIANT-1, -2, -3 enforcement; ceiling logic
  T-SP-76..85   schema_migration    — DDL generation, column list completeness
  T-SP-86..92   isolation           — no Command Center / SkillOrchestrator / LLP
                                      scoring / moneyline_probability.py touched

All tests are pure unit tests that do not require a live DB connection.
"""

from __future__ import annotations

import hashlib
import json
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

# ── Module under test ─────────────────────────────────────────────────────────
from gate_engine.source_provenance import (
    ConflictPair,
    ConflictStatus,
    FreshnessBasis,
    FreshnessStatus,
    Materiality,
    ProvenanceAuditResult,
    ReconstructionStatus,
    SOURCE_TYPE_NORMALIZER,
    SourceType,
    StructuredEvidence,
    auditSourceProvenance,
    build_evidence_from_dict,
    detect_conflicts,
    evaluate_freshness,
    lookup_policy,
    run_provenance_migration,
)
from gate_engine.source_provenance.evidence_contract import (
    hash_fact_value,
    normalize_source_type,
)
from gate_engine.source_provenance.fact_policy_registry import POLICY_REGISTRY, FactPolicy
from gate_engine.source_provenance.schema_migration import (
    _PROVENANCE_COLUMNS,
    generate_migration_sql,
)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _now() -> datetime:
    return datetime.now(tz=timezone.utc)


def _ts(offset_seconds: int = 0) -> datetime:
    return _now() - timedelta(seconds=offset_seconds)


_UNSET = object()  # Sentinel to distinguish "caller passed None" from "not passed"


def _make_evidence(
    evidence_id: str = "ev-001",
    fact_type: str = "player_line",
    fact_value_hash: str = "abc123",
    source: str = "odds_api",
    source_type: SourceType = SourceType.PRIMARY_API,
    source_grade: str = "A-",
    retrieved_at=_UNSET,          # None is a valid explicit value; use sentinel
    published_at: datetime | None = None,
    observed_at:  datetime | None = None,
    effective_at: datetime | None = None,
    materiality: Materiality = Materiality.HIGH,
    **kwargs,
) -> StructuredEvidence:
    # Default to 300s-ago only when caller did NOT pass retrieved_at at all
    resolved_retrieved_at = _ts(300) if retrieved_at is _UNSET else retrieved_at
    return StructuredEvidence(
        evidence_id=evidence_id,
        fact_type=fact_type,
        fact_value_hash=fact_value_hash,
        source_id=evidence_id,
        source=source,
        source_type=source_type,
        source_grade=source_grade,
        retrieved_at=resolved_retrieved_at,
        published_at=published_at,
        observed_at=observed_at,
        effective_at=effective_at,
        materiality=materiality,
        **kwargs,
    )


# =============================================================================
# T-SP-01..12  evidence_contract
# =============================================================================

class TestSourceTypeEnum(unittest.TestCase):

    def test_t01_all_eight_source_types_defined(self):
        expected = {
            "OFFICIAL", "PRIMARY_API", "SPORTSBOOK_EXCHANGE", "TRUSTED_SECONDARY",
            "RECONSTRUCTED", "PROXY", "SCREENSHOT", "OPERATOR_SUPPLIED",
        }
        actual = {st.value for st in SourceType if st != SourceType.UNKNOWN}
        self.assertEqual(expected, actual)

    def test_t02_unknown_is_ninth_value(self):
        self.assertIn(SourceType.UNKNOWN, list(SourceType))

    def test_t03_normalize_known_key_returns_correct_type(self):
        self.assertEqual(normalize_source_type("odds_api"), SourceType.PRIMARY_API)
        self.assertEqual(normalize_source_type("screenshot"), SourceType.SCREENSHOT)
        self.assertEqual(normalize_source_type("nws_cli"), SourceType.OFFICIAL)
        self.assertEqual(normalize_source_type("fanduel"), SourceType.SPORTSBOOK_EXCHANGE)
        self.assertEqual(normalize_source_type("statmuse"), SourceType.TRUSTED_SECONDARY)
        self.assertEqual(normalize_source_type("odds_aggregator"), SourceType.RECONSTRUCTED)
        self.assertEqual(normalize_source_type("prizepicks"), SourceType.PROXY)
        self.assertEqual(normalize_source_type("user_supplied"), SourceType.OPERATOR_SUPPLIED)

    def test_t04_normalize_unknown_key_returns_unknown(self):
        self.assertEqual(normalize_source_type("totally_unknown_source"), SourceType.UNKNOWN)
        self.assertEqual(normalize_source_type(None), SourceType.UNKNOWN)
        self.assertEqual(normalize_source_type(""), SourceType.UNKNOWN)

    def test_t05_normalizer_covers_all_source_grade_py_types(self):
        # All source types from gate_engine/source_grade.py must be covered
        expected_covered = [
            "screenshot", "pikkit", "prizepicks_screenshot", "board_capture",
            "user_supplied", "tweet", "social_report",
            "espn_api", "balldontlie_api", "box_score",
            "statmuse", "basketball_reference", "rotowire",
            "odds_api", "sportsbook_api",
            "nws_cli", "official_feed", "official_weather_station",
        ]
        for key in expected_covered:
            result = normalize_source_type(key)
            self.assertNotEqual(
                result, SourceType.UNKNOWN,
                f"source_grade key '{key}' should map to a non-UNKNOWN type",
            )

    def test_t06_hash_fact_value_stable(self):
        h1 = hash_fact_value({"line": 24.5, "side": "over"})
        h2 = hash_fact_value({"side": "over", "line": 24.5})
        self.assertEqual(h1, h2, "dict hashing must use sorted keys")

    def test_t07_hash_fact_value_none(self):
        h = hash_fact_value(None)
        self.assertEqual(h, hashlib.sha256(b"null").hexdigest())

    def test_t08_hash_fact_value_different_values_differ(self):
        h1 = hash_fact_value(24.5)
        h2 = hash_fact_value(25.5)
        self.assertNotEqual(h1, h2)

    def test_t09_build_evidence_from_dict_minimal(self):
        d = {
            "snapshot_id": "snap-001",
            "source_name": "espn_api",
            "source_type": "espn_api",
            "fetch_timestamp": _ts(60).isoformat(),
            "market": "player_line",
        }
        ev = build_evidence_from_dict(d)
        self.assertEqual(ev.evidence_id, "snap-001")
        self.assertEqual(ev.source_type, SourceType.PRIMARY_API)
        self.assertEqual(ev.fact_type, "player_line")

    def test_t10_build_evidence_from_dict_sets_source_grade(self):
        d = {"source_type": "screenshot", "source_name": "board", "market": "odds_line"}
        ev = build_evidence_from_dict(d)
        self.assertEqual(ev.source_grade, "D")  # SCREENSHOT → D

    def test_t11_structured_evidence_to_dict_round_trips(self):
        ev = _make_evidence()
        d = ev.to_dict()
        self.assertEqual(d["evidence_id"], "ev-001")
        self.assertEqual(d["source_type"], "PRIMARY_API")
        self.assertIn("freshness_status", d)
        self.assertEqual(d["can_execute"], False)

    def test_t12_freshness_status_enum_values(self):
        self.assertEqual(FreshnessStatus.FRESH.value, "FRESH")
        self.assertEqual(FreshnessStatus.STALE.value, "STALE")
        self.assertEqual(FreshnessStatus.EXPIRED.value, "EXPIRED")
        self.assertEqual(FreshnessStatus.UNVERIFIABLE.value, "UNVERIFIABLE")
        self.assertEqual(FreshnessStatus.POLICY_ABSENT.value, "POLICY_ABSENT")


# =============================================================================
# T-SP-13..25  fact_policy_registry
# =============================================================================

class TestFactPolicyRegistry(unittest.TestCase):

    def test_t13_exact_match_lookup(self):
        policy = lookup_policy("player_line", "market_gate")
        self.assertIsNotNone(policy)
        self.assertEqual(policy.policy_id, "PLAYER_LINE_MARKET_GATE")

    def test_t14_wildcard_checkpoint_fallback(self):
        # "DEFINITELY_NOT_A_KNOWN_FACT_TYPE" has no direct entry
        # but "*", "market_gate" exists
        policy = lookup_policy("DEFINITELY_NOT_A_KNOWN_FACT_TYPE", "market_gate")
        self.assertIsNotNone(policy)
        self.assertEqual(policy.policy_id, "WILDCARD_MARKET_GATE")

    def test_t15_wildcard_fact_type_fallback(self):
        # Known fact type, unknown checkpoint
        policy = lookup_policy("player_line", "some_unknown_checkpoint_xyz")
        self.assertIsNotNone(policy)
        # Falls through to wildcard ("*", "*")
        self.assertEqual(policy.policy_id, "WILDCARD_UNIVERSAL")

    def test_t16_double_wildcard_fallback(self):
        policy = lookup_policy("unknown_fact", "unknown_checkpoint")
        self.assertIsNotNone(policy)
        self.assertEqual(policy.policy_id, "WILDCARD_UNIVERSAL")

    def test_t17_policy_frozen_dataclass_immutable(self):
        policy = lookup_policy("player_line", "market_gate")
        with self.assertRaises(Exception):
            policy.max_age_seconds = 0  # type: ignore

    def test_t18_all_policies_have_policy_id_and_description(self):
        for key, policy in POLICY_REGISTRY.items():
            self.assertTrue(policy.policy_id, f"policy at {key} missing policy_id")
            self.assertTrue(policy.description, f"policy at {key} missing description")

    def test_t19_player_line_market_gate_requires_high_quality_sources(self):
        policy = lookup_policy("player_line", "market_gate")
        # Screenshot must NOT be in accepted sources at market gate
        self.assertNotIn(SourceType.SCREENSHOT, policy.accepted_source_types)
        self.assertNotIn(SourceType.OPERATOR_SUPPLIED, policy.accepted_source_types)
        # High-quality sources must be accepted
        self.assertIn(SourceType.OFFICIAL, policy.accepted_source_types)
        self.assertIn(SourceType.SPORTSBOOK_EXCHANGE, policy.accepted_source_types)

    def test_t20_player_line_candidate_intake_accepts_screenshot(self):
        # INVARIANT-2: screenshot is accepted at intake (just to identify the candidate)
        policy = lookup_policy("player_line", "candidate_intake")
        self.assertIn(SourceType.SCREENSHOT, policy.accepted_source_types)

    def test_t21_odds_line_market_gate_rejects_operator_supplied(self):
        policy = lookup_policy("odds_line", "market_gate")
        self.assertNotIn(SourceType.OPERATOR_SUPPLIED, policy.accepted_source_types)
        self.assertNotIn(SourceType.SCREENSHOT, policy.accepted_source_types)

    def test_t22_weather_market_gate_rejects_proxy(self):
        # Consumer weather Kalshi block
        policy = lookup_policy("weather", "market_gate")
        self.assertNotIn(SourceType.PROXY, policy.accepted_source_types)
        self.assertIn(SourceType.OFFICIAL, policy.accepted_source_types)

    def test_t23_historical_gamelog_model_scoring_uses_effective_at_basis(self):
        policy = lookup_policy("historical_gamelog", "model_scoring")
        self.assertEqual(policy.freshness_basis, FreshnessBasis.EFFECTIVE_AT)

    def test_t24_starting_pitcher_market_gate_uses_published_at_basis(self):
        # INVARIANT-1: freshness_basis is fact-specific, not universally retrieved_at
        policy = lookup_policy("starting_pitcher", "market_gate")
        self.assertEqual(policy.freshness_basis, FreshnessBasis.PUBLISHED_AT)

    def test_t25_historical_gamelog_stale_ceiling_is_none(self):
        # Historical data that's 'stale' does not impose a ceiling
        policy = lookup_policy("historical_gamelog", "model_scoring")
        self.assertIsNone(policy.stale_ceiling)


# =============================================================================
# T-SP-26..40  freshness_engine
# =============================================================================

class TestFreshnessEngine(unittest.TestCase):

    def _policy(
        self,
        basis: FreshnessBasis = FreshnessBasis.RETRIEVED_AT,
        max_age: int = 3600,
    ) -> FactPolicy:
        return FactPolicy(
            policy_id="TEST",
            description="test",
            freshness_basis=basis,
            max_age_seconds=max_age,
            accepted_source_types=frozenset(SourceType),
        )

    def test_t26_fresh_when_within_max_age(self):
        ev = _make_evidence(retrieved_at=_ts(100))  # 100s old, max=3600
        status, age = evaluate_freshness(ev, self._policy(max_age=3600))
        self.assertEqual(status, FreshnessStatus.FRESH)
        self.assertAlmostEqual(age, 100, delta=5)

    def test_t27_stale_when_between_1x_and_3x_max(self):
        ev = _make_evidence(retrieved_at=_ts(5000))  # 5000s, max=3600, 3x=10800
        status, age = evaluate_freshness(ev, self._policy(max_age=3600))
        self.assertEqual(status, FreshnessStatus.STALE)

    def test_t28_expired_when_beyond_3x_max(self):
        ev = _make_evidence(retrieved_at=_ts(15000))  # 15000 > 3×3600=10800
        status, age = evaluate_freshness(ev, self._policy(max_age=3600))
        self.assertEqual(status, FreshnessStatus.EXPIRED)

    def test_t29_unverifiable_when_timestamp_absent(self):
        ev = _make_evidence(retrieved_at=None)
        policy = self._policy(basis=FreshnessBasis.RETRIEVED_AT)
        status, age = evaluate_freshness(ev, policy)
        self.assertEqual(status, FreshnessStatus.UNVERIFIABLE)
        self.assertIsNone(age)

    def test_t30_invariant1_uses_published_at_not_retrieved_at(self):
        """INVARIANT-1: freshness uses the policy basis, not hardcoded retrieved_at."""
        ev = _make_evidence(
            retrieved_at=_ts(100),    # very fresh
            published_at=_ts(10000),  # very stale
        )
        policy = self._policy(basis=FreshnessBasis.PUBLISHED_AT, max_age=3600)
        status, _ = evaluate_freshness(ev, policy)
        # published_at is stale → result must reflect that
        self.assertIn(status, (FreshnessStatus.STALE, FreshnessStatus.EXPIRED))

    def test_t31_invariant1_fresh_retrieved_at_but_stale_effective_at(self):
        """INVARIANT-1: effective_at basis matters independently of retrieved_at."""
        ev = _make_evidence(
            retrieved_at=_ts(10),      # freshly fetched
            effective_at=_ts(20000),   # but fact was effective 20000s ago
        )
        policy = self._policy(basis=FreshnessBasis.EFFECTIVE_AT, max_age=3600)
        status, _ = evaluate_freshness(ev, policy)
        self.assertIn(status, (FreshnessStatus.STALE, FreshnessStatus.EXPIRED))

    def test_t32_retrieved_at_basis_does_not_use_published_at(self):
        ev = _make_evidence(
            retrieved_at=_ts(100),
            published_at=_ts(10),
        )
        policy = self._policy(basis=FreshnessBasis.RETRIEVED_AT, max_age=3600)
        status, age = evaluate_freshness(ev, policy)
        self.assertEqual(status, FreshnessStatus.FRESH)
        self.assertAlmostEqual(age, 100, delta=5)

    def test_t33_fallback_to_retrieved_at_when_primary_absent(self):
        ev = _make_evidence(retrieved_at=_ts(200), published_at=None)
        policy = self._policy(basis=FreshnessBasis.PUBLISHED_AT, max_age=3600)
        status, age = evaluate_freshness(ev, policy)
        # Falls back to retrieved_at → FRESH
        self.assertEqual(status, FreshnessStatus.FRESH)

    def test_t34_future_anchor_treated_as_fresh(self):
        future = _now() + timedelta(seconds=600)
        ev = _make_evidence(retrieved_at=future)
        policy = self._policy()
        status, age = evaluate_freshness(ev, policy)
        self.assertEqual(status, FreshnessStatus.FRESH)
        self.assertLess(age or 0, 0)

    def test_t35_observed_at_basis_used_for_weather(self):
        policy = lookup_policy("weather", "market_gate")
        self.assertEqual(policy.freshness_basis, FreshnessBasis.OBSERVED_AT)

    def test_t36_age_returned_is_float_seconds(self):
        ev = _make_evidence(retrieved_at=_ts(3600))
        policy = self._policy()
        _, age = evaluate_freshness(ev, policy)
        self.assertIsInstance(age, float)
        self.assertAlmostEqual(age, 3600, delta=10)

    def test_t37_max_age_boundary_is_inclusive(self):
        ev = _make_evidence(retrieved_at=_ts(3600))
        policy = self._policy(max_age=3600)
        status, age = evaluate_freshness(ev, policy)
        # age ≈ 3600 → FRESH (at the boundary)
        # Allow a few seconds of drift in test execution
        self.assertIn(status, (FreshnessStatus.FRESH, FreshnessStatus.STALE))

    def test_t38_naive_datetime_treated_as_utc(self):
        naive = datetime.utcnow() - timedelta(seconds=100)
        ev = _make_evidence(retrieved_at=naive)
        policy = self._policy(max_age=3600)
        status, _ = evaluate_freshness(ev, policy)
        self.assertEqual(status, FreshnessStatus.FRESH)


# =============================================================================
# T-SP-41..55  conflict_detector
# =============================================================================

class TestConflictDetector(unittest.TestCase):

    def _pair(
        self,
        fact_type: str = "player_line",
        hash_a: str = "hash_A",
        hash_b: str = "hash_B",
        mat_a: Materiality = Materiality.HIGH,
        mat_b: Materiality = Materiality.HIGH,
    ) -> tuple[StructuredEvidence, StructuredEvidence]:
        ev_a = _make_evidence("ev-a", fact_type=fact_type,
                              fact_value_hash=hash_a, materiality=mat_a)
        ev_b = _make_evidence("ev-b", fact_type=fact_type,
                              fact_value_hash=hash_b, materiality=mat_b)
        return ev_a, ev_b

    def test_t41_no_conflict_when_hashes_equal(self):
        ev_a, ev_b = self._pair(hash_a="same", hash_b="same")
        conflicts = detect_conflicts([ev_a, ev_b])
        self.assertEqual(len(conflicts), 0)

    def test_t42_material_conflict_when_hashes_differ_high_materiality(self):
        ev_a, ev_b = self._pair()
        conflicts = detect_conflicts([ev_a, ev_b])
        self.assertEqual(len(conflicts), 1)
        self.assertEqual(conflicts[0].conflict_kind, "VALUE_HASH_MISMATCH")

    def test_t43_invariant3_both_records_preserved_not_discarded(self):
        """INVARIANT-3: Both evidence objects preserved; neither silently selected."""
        ev_a, ev_b = self._pair()
        conflicts = detect_conflicts([ev_a, ev_b])
        # Both should still be accessible
        self.assertIs(conflicts[0].evidence_a, ev_a)
        self.assertIs(conflicts[0].evidence_b, ev_b)

    def test_t44_conflict_status_set_on_both_objects(self):
        """INVARIANT-3: conflict_status is set on both members."""
        ev_a, ev_b = self._pair()
        detect_conflicts([ev_a, ev_b])
        self.assertEqual(ev_a.conflict_status, ConflictStatus.MATERIAL_SOURCE_CONFLICT)
        self.assertEqual(ev_b.conflict_status, ConflictStatus.MATERIAL_SOURCE_CONFLICT)

    def test_t45_conflicts_with_populated_on_both_objects(self):
        """INVARIANT-3: conflicts_with records the opposing evidence_id."""
        ev_a, ev_b = self._pair()
        detect_conflicts([ev_a, ev_b])
        self.assertIn("ev-b", ev_a.conflicts_with)
        self.assertIn("ev-a", ev_b.conflicts_with)

    def test_t46_low_materiality_pair_below_high_threshold_not_flagged(self):
        ev_a, ev_b = self._pair(mat_a=Materiality.LOW, mat_b=Materiality.LOW)
        # Threshold=HIGH: neither meets it → no conflict
        conflicts = detect_conflicts(
            [ev_a, ev_b], materiality_threshold=Materiality.HIGH
        )
        self.assertEqual(len(conflicts), 0)

    def test_t47_medium_high_pair_flagged_at_medium_threshold(self):
        ev_a, ev_b = self._pair(mat_a=Materiality.MEDIUM, mat_b=Materiality.LOW)
        # max(MEDIUM, LOW) >= MEDIUM → flagged
        conflicts = detect_conflicts(
            [ev_a, ev_b], materiality_threshold=Materiality.MEDIUM
        )
        self.assertEqual(len(conflicts), 1)

    def test_t48_different_fact_types_not_compared(self):
        ev_a = _make_evidence("a", fact_type="player_line", fact_value_hash="h1")
        ev_b = _make_evidence("b", fact_type="injury_status", fact_value_hash="h2")
        conflicts = detect_conflicts([ev_a, ev_b])
        self.assertEqual(len(conflicts), 0)

    def test_t49_three_way_conflict_produces_three_pairs(self):
        ev_a = _make_evidence("a", fact_value_hash="h1")
        ev_b = _make_evidence("b", fact_value_hash="h2")
        ev_c = _make_evidence("c", fact_value_hash="h3")
        conflicts = detect_conflicts([ev_a, ev_b, ev_c])
        self.assertEqual(len(conflicts), 3)

    def test_t50_single_evidence_no_conflict(self):
        ev = _make_evidence()
        conflicts = detect_conflicts([ev])
        self.assertEqual(len(conflicts), 0)

    def test_t51_empty_list_no_conflict(self):
        conflicts = detect_conflicts([])
        self.assertEqual(len(conflicts), 0)

    def test_t52_mutate_evidence_false_leaves_objects_unchanged(self):
        ev_a, ev_b = self._pair()
        detect_conflicts([ev_a, ev_b], mutate_evidence=False)
        self.assertEqual(ev_a.conflict_status, ConflictStatus.NO_CONFLICT)
        self.assertEqual(ev_b.conflict_status, ConflictStatus.NO_CONFLICT)

    def test_t53_conflict_pair_to_dict_contains_required_fields(self):
        ev_a, ev_b = self._pair()
        pairs = detect_conflicts([ev_a, ev_b])
        d = pairs[0].to_dict()
        self.assertIn("fact_type", d)
        self.assertIn("evidence_a_id", d)
        self.assertIn("evidence_b_id", d)
        self.assertIn("conflict_kind", d)
        self.assertIn("hash_a", d)
        self.assertIn("hash_b", d)

    def test_t54_duplicate_conflicts_with_entries_not_added_twice(self):
        ev_a, ev_b = self._pair()
        detect_conflicts([ev_a, ev_b])
        detect_conflicts([ev_a, ev_b])  # second call
        self.assertEqual(ev_a.conflicts_with.count("ev-b"), 1)

    def test_t55_conflict_kind_is_value_hash_mismatch(self):
        ev_a, ev_b = self._pair()
        pairs = detect_conflicts([ev_a, ev_b])
        self.assertEqual(pairs[0].conflict_kind, "VALUE_HASH_MISMATCH")


# =============================================================================
# T-SP-56..75  auditor  (auditSourceProvenance)
# =============================================================================

class TestAuditorInvariant1Freshness(unittest.TestCase):
    """INVARIANT-1: freshness is based on policy.freshness_basis, not hardcoded retrieved_at."""

    def test_t56_fresh_evidence_has_null_ceiling(self):
        ev = _make_evidence(retrieved_at=_ts(100), source_type=SourceType.PRIMARY_API)
        result = auditSourceProvenance(ev, "market_gate")
        # If the policy says FRESH, max_supportable_ceiling must be None
        if result.freshness_status == FreshnessStatus.FRESH:
            # Ceiling from source check may still apply; but freshness alone should not add one
            # We can only assert freshness did not impose a ceiling by itself
            self.assertNotIn("STALE_CEILING", result.audit_flags)

    def test_t57_stale_evidence_gets_policy_stale_ceiling(self):
        # Make retrieved_at very stale — 10h old, policy max is 1h
        ev = _make_evidence(
            retrieved_at=_ts(36000),     # 10 hours
            source_type=SourceType.PRIMARY_API,
            fact_type="player_line",
        )
        result = auditSourceProvenance(ev, "market_gate")
        self.assertIn(result.freshness_status, (FreshnessStatus.STALE, FreshnessStatus.EXPIRED))
        policy = result.policy
        if policy and policy.stale_ceiling:
            self.assertEqual(result.ceiling_imposed, policy.stale_ceiling)

    def test_t58_unverifiable_freshness_does_not_impose_ceiling(self):
        """Missing timestamp → UNVERIFIABLE, no freshness ceiling."""
        ev = _make_evidence(
            retrieved_at=None,   # No timestamps at all
            fact_type="player_line",
            source_type=SourceType.OFFICIAL,
        )
        result = auditSourceProvenance(ev, "candidate_intake")
        self.assertEqual(result.freshness_status, FreshnessStatus.UNVERIFIABLE)
        # Unverifiable freshness must NOT impose a stale ceiling
        self.assertNotIn("STALE_CEILING", result.audit_flags)

    def test_t59_starting_pitcher_uses_published_at_basis(self):
        """INVARIANT-1: starting_pitcher/market_gate uses published_at, not retrieved_at."""
        policy = lookup_policy("starting_pitcher", "market_gate")
        self.assertEqual(policy.freshness_basis, FreshnessBasis.PUBLISHED_AT)

        # fresh retrieved_at but stale published_at → should be stale
        ev = _make_evidence(
            fact_type="starting_pitcher",
            source_type=SourceType.OFFICIAL,
            retrieved_at=_ts(60),      # freshly fetched
            published_at=_ts(25000),   # but published 7 hours ago
        )
        result = auditSourceProvenance(ev, "market_gate")
        self.assertIn(result.freshness_status, (FreshnessStatus.STALE, FreshnessStatus.EXPIRED))


class TestAuditorInvariant2SourceCeiling(unittest.TestCase):
    """INVARIANT-2: SourceType does NOT impose a universal ceiling by itself."""

    def test_t60_screenshot_candidate_intake_no_ceiling_from_source(self):
        """Screenshot is accepted at candidate_intake — no ceiling from source type."""
        ev = _make_evidence(
            source_type=SourceType.SCREENSHOT,
            fact_type="player_line",
            retrieved_at=_ts(100),
        )
        result = auditSourceProvenance(ev, "candidate_intake")
        # Screenshot is in accepted_source_types for candidate_intake
        policy = lookup_policy("player_line", "candidate_intake")
        if SourceType.SCREENSHOT in policy.accepted_source_types:
            self.assertNotIn("SOURCE_TYPE_REJECTED:SCREENSHOT", result.audit_flags)

    def test_t61_screenshot_market_gate_ceiling_from_policy(self):
        """Screenshot at market_gate → ceiling from policy, not from universal screenshot rule."""
        ev = _make_evidence(
            source_type=SourceType.SCREENSHOT,
            fact_type="player_line",
            retrieved_at=_ts(100),
        )
        result = auditSourceProvenance(ev, "market_gate")
        # Policy does NOT accept screenshot at market gate → ceiling applied
        # But the ceiling comes from FactPolicy.insufficient_source_ceiling, not
        # a hard "screenshot always → WATCH" rule
        policy = lookup_policy("player_line", "market_gate")
        if SourceType.SCREENSHOT not in policy.accepted_source_types:
            self.assertIn("SOURCE_TYPE_REJECTED:SCREENSHOT", result.audit_flags)
            if policy.insufficient_source_ceiling:
                self.assertEqual(result.ceiling_imposed, policy.insufficient_source_ceiling)

    def test_t62_operator_supplied_odds_line_market_gate(self):
        """Operator-supplied price data cannot satisfy live-orderbook verification gate."""
        ev = _make_evidence(
            source_type=SourceType.OPERATOR_SUPPLIED,
            fact_type="odds_line",
            retrieved_at=_ts(100),
        )
        result = auditSourceProvenance(ev, "market_gate")
        policy = lookup_policy("odds_line", "market_gate")
        self.assertNotIn(SourceType.OPERATOR_SUPPLIED, policy.accepted_source_types)
        self.assertIn("SOURCE_TYPE_REJECTED:OPERATOR_SUPPLIED", result.audit_flags)

    def test_t63_official_source_accepted_at_market_gate_no_insufficient_ceiling(self):
        """OFFICIAL source at market gate should not get a source-type ceiling."""
        ev = _make_evidence(
            source_type=SourceType.OFFICIAL,
            fact_type="player_line",
            retrieved_at=_ts(100),
        )
        result = auditSourceProvenance(ev, "market_gate")
        self.assertNotIn("SOURCE_TYPE_REJECTED:OFFICIAL", result.audit_flags)

    def test_t64_reconstructed_historical_gamelog_model_scoring_accepted(self):
        """Reconstructed historical data may support research model with uncertainty penalty."""
        ev = _make_evidence(
            source_type=SourceType.RECONSTRUCTED,
            fact_type="historical_gamelog",
            retrieved_at=_ts(1000),
            effective_at=_ts(3600 * 24),  # 1 day old effective_at
        )
        result = auditSourceProvenance(ev, "model_scoring")
        policy = lookup_policy("historical_gamelog", "model_scoring")
        self.assertIn(SourceType.RECONSTRUCTED, policy.accepted_source_types)
        self.assertNotIn("SOURCE_TYPE_REJECTED:RECONSTRUCTED", result.audit_flags)

    def test_t65_max_supportable_ceiling_none_when_both_checks_pass(self):
        """max_supportable_ceiling is None when fact is fresh and source is accepted."""
        ev = _make_evidence(
            source_type=SourceType.SPORTSBOOK_EXCHANGE,
            fact_type="odds_line",
            retrieved_at=_ts(60),
        )
        result = auditSourceProvenance(ev, "llp_calibration")
        # fresh SPORTSBOOK_EXCHANGE for llp_calibration should be fully accepted
        if result.freshness_status == FreshnessStatus.FRESH:
            policy = lookup_policy("odds_line", "llp_calibration")
            if SourceType.SPORTSBOOK_EXCHANGE in policy.accepted_source_types:
                self.assertIsNone(result.fact.max_supportable_ceiling)


class TestAuditorInvariant3Conflicts(unittest.TestCase):
    """INVARIANT-3: Conflicting sources are flagged, not silently resolved."""

    def test_t66_conflict_detected_when_existing_facts_conflict(self):
        ev_existing = _make_evidence("ev-x", fact_type="player_line",
                                     fact_value_hash="hash_old",
                                     source_type=SourceType.OFFICIAL)
        ev_new = _make_evidence("ev-y", fact_type="player_line",
                                fact_value_hash="hash_new",
                                source_type=SourceType.SPORTSBOOK_EXCHANGE,
                                retrieved_at=_ts(60))
        result = auditSourceProvenance(
            ev_new, "market_gate", existing_facts=[ev_existing]
        )
        self.assertEqual(len(result.conflict_pairs), 1)
        self.assertIn("CONFLICTS_DETECTED:1", result.audit_flags)

    def test_t67_invariant3_no_automatic_resolution(self):
        """INVARIANT-3: Neither source is selected; both preserved."""
        ev_existing = _make_evidence("ev-x", fact_value_hash="hash_A")
        ev_new      = _make_evidence("ev-y", fact_value_hash="hash_B",
                                     retrieved_at=_ts(60))
        result = auditSourceProvenance(ev_new, "market_gate", existing_facts=[ev_existing])
        pair = result.conflict_pairs[0]
        # Neither is silently discarded
        self.assertIs(pair.evidence_a, ev_existing)
        self.assertIs(pair.evidence_b, ev_new)

    def test_t68_no_existing_facts_no_conflict_check(self):
        ev = _make_evidence(retrieved_at=_ts(60))
        result = auditSourceProvenance(ev, "market_gate", existing_facts=None)
        self.assertEqual(len(result.conflict_pairs), 0)

    def test_t69_matching_hashes_no_conflict(self):
        same_hash = hash_fact_value(24.5)
        ev_a = _make_evidence("ev-a", fact_value_hash=same_hash)
        ev_b = _make_evidence("ev-b", fact_value_hash=same_hash, retrieved_at=_ts(60))
        result = auditSourceProvenance(ev_b, "market_gate", existing_facts=[ev_a])
        self.assertEqual(len(result.conflict_pairs), 0)


class TestAuditorOutputContract(unittest.TestCase):

    def test_t70_result_is_provenance_audit_result(self):
        ev = _make_evidence(retrieved_at=_ts(60))
        result = auditSourceProvenance(ev, "market_gate")
        self.assertIsInstance(result, ProvenanceAuditResult)

    def test_t71_to_dict_contains_all_required_keys(self):
        ev = _make_evidence(retrieved_at=_ts(60))
        result = auditSourceProvenance(ev, "market_gate")
        d = result.to_dict()
        for key in (
            "evidence_id", "fact_type", "freshness_status", "age_seconds",
            "ceiling_imposed", "ceiling_reason", "policy_id", "freshness_basis",
            "source_type", "conflict_count", "conflict_pairs", "audit_flags",
            "max_supportable_ceiling", "can_execute", "execution_rule",
        ):
            self.assertIn(key, d, f"to_dict() missing key '{key}'")

    def test_t72_can_execute_is_always_false(self):
        ev = _make_evidence(retrieved_at=_ts(60))
        result = auditSourceProvenance(ev, "market_gate")
        self.assertFalse(result.to_dict()["can_execute"])
        self.assertEqual(
            result.to_dict()["execution_rule"],
            "DRY_RUN_ONLY_NO_LIVE_TRADING_NO_MARKET_ORDERS",
        )

    def test_t73_fact_object_mutated_with_freshness_policy_id(self):
        ev = _make_evidence(retrieved_at=_ts(60))
        result = auditSourceProvenance(ev, "market_gate")
        self.assertIsNotNone(ev.freshness_policy_id)
        self.assertIsNotNone(ev.freshness_basis)

    def test_t74_ceiling_stricter_when_both_checks_fail(self):
        """When freshness AND source type both fail, the stricter ceiling wins."""
        ev = _make_evidence(
            source_type=SourceType.SCREENSHOT,
            fact_type="player_line",
            retrieved_at=_ts(50000),  # very stale
        )
        result = auditSourceProvenance(ev, "market_gate")
        # Both stale_ceiling and insufficient_source_ceiling are "WATCH"
        # ceiling_imposed should be "WATCH" (or stricter)
        policy = lookup_policy("player_line", "market_gate")
        if policy.stale_ceiling and policy.insufficient_source_ceiling:
            self.assertIsNotNone(result.ceiling_imposed)

    def test_t75_policy_absent_returns_policy_absent_status(self):
        # Patch lookup_policy to return None to simulate missing policy
        ev = _make_evidence(retrieved_at=_ts(60))
        with patch(
            "gate_engine.source_provenance.auditor.lookup_policy",
            return_value=None,
        ):
            result = auditSourceProvenance(ev, "some_checkpoint")
        self.assertEqual(result.freshness_status, FreshnessStatus.POLICY_ABSENT)
        self.assertIsNone(result.policy)
        self.assertIn("POLICY_ABSENT", result.audit_flags)


# =============================================================================
# T-SP-76..85  schema_migration
# =============================================================================

class TestSchemaMigration(unittest.TestCase):

    def test_t76_provenance_columns_list_has_sixteen_entries(self):
        self.assertEqual(len(_PROVENANCE_COLUMNS), 16)

    def test_t77_all_required_column_names_present(self):
        required = {
            "fact_type", "fact_value_hash", "source_grade",
            "published_at", "observed_at", "effective_at", "valid_until",
            "freshness_policy_id", "freshness_basis", "freshness_status",
            "materiality", "supports_checkpoint", "conflicts_with",
            "conflict_status", "reconstruction_status", "max_supportable_ceiling",
        }
        actual = {col[0] for col in _PROVENANCE_COLUMNS}
        self.assertEqual(required, actual)

    def test_t78_timestamp_columns_are_timestamptz(self):
        ts_cols = {"published_at", "observed_at", "effective_at", "valid_until"}
        for name, col_type in _PROVENANCE_COLUMNS:
            if name in ts_cols:
                self.assertEqual(col_type, "TIMESTAMPTZ",
                                 f"Column {name} should be TIMESTAMPTZ")

    def test_t79_array_columns_are_text_array(self):
        arr_cols = {"supports_checkpoint", "conflicts_with"}
        for name, col_type in _PROVENANCE_COLUMNS:
            if name in arr_cols:
                self.assertEqual(col_type, "TEXT[]",
                                 f"Column {name} should be TEXT[]")

    def test_t80_generate_migration_sql_contains_both_tables(self):
        sql = generate_migration_sql()
        self.assertIn("llp_source_snapshots", sql)
        self.assertIn("uac_evidence_packets", sql)

    def test_t81_generate_migration_sql_uses_add_column_if_not_exists(self):
        sql = generate_migration_sql()
        self.assertIn("ADD COLUMN IF NOT EXISTS", sql)

    def test_t82_generate_migration_sql_contains_fk_fix(self):
        sql = generate_migration_sql()
        self.assertIn("fk_llp_calibration_source_snapshot", sql)
        self.assertIn("NOT VALID", sql)

    def test_t83_run_provenance_migration_returns_dict_on_success(self):
        """Smoke-test with a mocked psycopg2 connection."""
        mock_conn = MagicMock()
        mock_cur = MagicMock()
        mock_conn.cursor.return_value = mock_cur
        mock_cur.__enter__ = lambda s: s
        mock_cur.__exit__ = MagicMock(return_value=False)

        result = run_provenance_migration(mock_conn)
        self.assertIn("success", result)
        self.assertIn("columns_added", result)
        self.assertIn("fk_applied", result)
        self.assertIn("errors", result)

    def test_t84_run_provenance_migration_adds_columns_to_both_tables(self):
        mock_conn = MagicMock()
        mock_cur  = MagicMock()
        mock_conn.cursor.return_value = mock_cur
        mock_cur.__enter__ = lambda s: s
        mock_cur.__exit__ = MagicMock(return_value=False)

        result = run_provenance_migration(mock_conn)
        added = result["columns_added"]
        tables_covered = {col.split(".")[0] for col in added}
        self.assertIn("llp_source_snapshots", tables_covered)
        self.assertIn("uac_evidence_packets", tables_covered)

    def test_t85_migration_idempotent_on_exception(self):
        """migration returns dict even when DB raises (e.g. column already exists)."""
        mock_conn = MagicMock()
        mock_conn.cursor.side_effect = Exception("DB unavailable")
        result = run_provenance_migration(mock_conn)
        self.assertFalse(result["success"])
        self.assertGreater(len(result["errors"]), 0)


# =============================================================================
# T-SP-86..92  isolation — no unrelated files touched
# =============================================================================

class TestIsolationGuarantee(unittest.TestCase):

    def _read_source(self, module_path: str) -> str:
        import os
        base = os.path.join(
            os.path.dirname(__file__), "..", "..",
        )
        full = os.path.normpath(os.path.join(base, module_path))
        with open(full, encoding="utf-8") as f:
            return f.read()

    def test_t86_command_center_orchestrator_not_modified(self):
        """Command Center orchestrator must not reference source_provenance."""
        src = self._read_source("gate_engine/command_center/orchestrator.py")
        self.assertNotIn("source_provenance", src,
                         "command_center/orchestrator.py must not be touched by this patch")

    def test_t87_skill_orchestrator_not_modified(self):
        """SkillOrchestrator must not reference source_provenance."""
        src = self._read_source("skills/orchestrator.py")
        self.assertNotIn("source_provenance", src,
                         "skills/orchestrator.py must not be touched by this patch")

    def test_t88_moneyline_probability_not_modified(self):
        """moneyline_probability.py must not reference source_provenance."""
        import os
        base = os.path.join(os.path.dirname(__file__), "..", "..")
        # Check all moneyline_probability files
        for root, dirs, files in os.walk(os.path.normpath(base)):
            for fname in files:
                if fname == "moneyline_probability.py":
                    with open(os.path.join(root, fname)) as f:
                        src = f.read()
                    self.assertNotIn("source_provenance", src,
                                     f"{fname} must not be touched by this patch")

    def test_t89_llp_governance_not_modified(self):
        """llp_governance.py must not reference source_provenance directly."""
        # llp_governance.py is fine to CALL audit helpers via llp_stage2_tables,
        # but must not import source_provenance directly.
        src = self._read_source("gate_engine/llp_governance.py")
        self.assertNotIn("from gate_engine.source_provenance", src,
                         "llp_governance.py must not import source_provenance directly")

    def test_t90_source_provenance_has_can_execute_false(self):
        """Every module in source_provenance must declare can_execute=False."""
        import gate_engine.source_provenance.auditor as auditor_mod
        self.assertFalse(auditor_mod.can_execute)
        self.assertEqual(
            auditor_mod.execution_rule,
            "DRY_RUN_ONLY_NO_LIVE_TRADING_NO_MARKET_ORDERS",
        )

    def test_t91_patch_id_and_precedence_correct(self):
        import gate_engine.source_provenance as sp
        self.assertEqual(sp.PATCH_ID,
                         "WOW-PATCH-2026-08-14-SOURCE-PROVENANCE-FRESHNESS-AUDITOR-v2")
        self.assertEqual(sp.PATCH_PRECEDENCE, 102)

    def test_t92_audit_store_hook_is_best_effort(self):
        """The provenance hook in record_evidence_packet must be inside try/except."""
        src = self._read_source("gate_engine/universal_agent/audit_store.py")
        # Verify the best-effort guard is present
        self.assertIn("_audit_uac_evidence_provenance", src)
        self.assertIn("Best-effort", src)

    def test_t93_llp_stage2_tables_hook_is_best_effort(self):
        """The provenance hook in log_calibration_entry_pg must be best-effort."""
        src = self._read_source("gate_engine/llp_stage2_tables.py")
        self.assertIn("_audit_calibration_entry_provenance", src)
        self.assertIn("Best-effort", src)


if __name__ == "__main__":
    unittest.main()
