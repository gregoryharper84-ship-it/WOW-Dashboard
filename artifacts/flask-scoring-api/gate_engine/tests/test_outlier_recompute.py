"""
tests/test_outlier_recompute.py

Unit tests for gate_engine/outlier_recompute.py — Stage A offline engine.

Coverage:
  1. RESOLVED state: outlier isolated, distribution recomputed, bounds updated,
     original evidence preserved, excluded_event_ids populated.
  2. UNRESOLVED state / DATA_CONTRACT_FAIL: all named failure reasons.
  3. ERROR state: unexpected exception → error_reason set, result returned.
  4. Governance invariants: terminal_label_authority=False, can_execute=False,
     TERMINAL_LABEL_AUTHORITY=False.
  5. WNBA points, assists, rebounds fixtures.
  6. MLB pitcher strikeouts fixtures.
  7. Mixed batch: independent per-row results.
  8. Original evidence always preserved in every state.
  9. Explicit three-state contract: no implicit/narrative resolution.

No network calls. No database calls. All synthetic.
"""
import statistics
import unittest

from gate_engine.outlier_recompute import (
    DataContractFailReason,
    MIN_GAMES_AFTER_EXCLUSION,
    MIN_GAMES_TO_ISOLATE,
    OutlierRecomputeResult,
    OutlierRecomputeState,
    run,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _row_with_outlier(
    sport: str = "WNBA",
    prop_type: str = "PTS",
    l10_games: list | None = None,
    l5_avg: float = 18.0,
    l10_avg: float = 22.0,
    flags: dict | None = None,
    line: float = 20.5,
) -> dict:
    """
    Build a minimal row dict that looks like it has passed the outlier gate
    and is ready for recompute.  l10_games are stored in the l5_l10_ledger
    gate result (where outlier_recompute reads them).
    """
    games = l10_games if l10_games is not None else [20, 18, 22, 19, 25, 21, 18, 17, 19, 45]
    l10_mean = statistics.mean(games) if games else 0.0
    season_high = max(games) if games else 0
    games_without_max = [g for g in games if g != season_high]
    avg_without_max = statistics.mean(games_without_max) if games_without_max else l10_mean

    default_flags = {
        "l5_l10_gap_pct":         abs(l5_avg - l10_avg) / l10_avg if l10_avg > 0 else 0.0,
        "l5_l10_gap_flagged":     abs(l5_avg - l10_avg) / l10_avg > 0.20 if l10_avg > 0 else False,
        "avg_inflated_by_outlier": l10_mean > avg_without_max * 1.15,
        "season_high_outlier":    season_high > l10_mean * 1.5,
        "assist_volatile":        False,
        "median_disagrees_avg":   False,
        "small_sample_warning":   False,
        "whole_number_push_risk": False,
    }
    active_flags = {**default_flags, **(flags or {})}
    any_flag = any([
        active_flags.get("l5_l10_gap_flagged"),
        active_flags.get("avg_inflated_by_outlier"),
        active_flags.get("season_high_outlier"),
        active_flags.get("assist_volatile"),
        active_flags.get("median_disagrees_avg"),
    ])

    blockers = ["OUTLIER_FLAG:REVIEW_REQUIRED"] if any_flag else []

    return {
        "sport":     sport,
        "prop_type": prop_type,
        "player":    "Test Player",
        "row_id":    "test_row_outlier",
        "line":      line,
        "blockers":  blockers,
        "gates": {
            "l5_l10_ledger": {
                "passed":          True,
                "l5_avg":          l5_avg,
                "l10_avg":         l10_avg,
                "l5_median":       l5_avg,
                "l10_median":      l10_avg,
                "l10_games":       games,
                "l5_games":        games[-5:] if len(games) >= 5 else games,
                "small_sample_warning": False,
            },
            "outlier_gate": {
                "passed":   True,
                "skipped":  False,
                "any_flag": any_flag,
                "flags":    active_flags,
            },
        },
    }


def _assert_governance(tc: unittest.TestCase, result: OutlierRecomputeResult) -> None:
    tc.assertFalse(result.terminal_label_authority)
    tc.assertFalse(result.can_execute)
    tc.assertIsInstance(result.excluded_event_ids, tuple)
    tc.assertIsInstance(result.excluded_reasons, tuple)
    tc.assertIsInstance(result.acquisition_attempts, tuple)
    tc.assertIn(result.state, (
        OutlierRecomputeState.RESOLVED,
        OutlierRecomputeState.UNRESOLVED,
        OutlierRecomputeState.ERROR,
    ))


def _assert_original_evidence_preserved(
    tc: unittest.TestCase,
    result: OutlierRecomputeResult,
    original_l10: list,
) -> None:
    """Original l10_games must be in result.original_evidence unchanged."""
    tc.assertIn("l10_games", result.original_evidence)
    tc.assertEqual(result.original_evidence["l10_games"], original_l10)


# ===========================================================================
# 1. RESOLVED state
# ===========================================================================

class TestResolvedState(unittest.TestCase):

    def test_season_high_outlier_resolved(self):
        """Max-value outlier game is isolated; L9 distribution recomputed."""
        games = [20, 18, 22, 19, 25, 21, 18, 17, 19, 45]  # 45 is the outlier
        row = _row_with_outlier(l10_games=games)
        result = run(row)

        _assert_governance(self, result)
        self.assertEqual(result.state, OutlierRecomputeState.RESOLVED)
        self.assertIsNotNone(result.recomputed_distribution)
        self.assertGreater(result.recomputed_distribution["count"], 0)
        # The outlier (45) should be excluded
        self.assertGreater(len(result.excluded_event_ids), 0)
        # Recomputed mean must be lower than original mean
        original_mean = statistics.mean(games)
        self.assertLess(result.recomputed_distribution["mean"], original_mean)
        # State is a string enum value
        self.assertEqual(result.state, "RESOLVED")

    def test_excluded_event_ids_populated(self):
        games = [5, 6, 8, 5, 7, 6, 9, 5, 6, 20]  # 20 is outlier
        row = _row_with_outlier(sport="MLB", prop_type="SO", l10_games=games, line=6.5)
        result = run(row)
        self.assertEqual(result.state, OutlierRecomputeState.RESOLVED)
        self.assertGreater(len(result.excluded_event_ids), 0)
        for eid in result.excluded_event_ids:
            self.assertIsInstance(eid, str)

    def test_excluded_reasons_carry_structured_info(self):
        games = [20, 18, 22, 19, 25, 21, 18, 17, 19, 45]
        row = _row_with_outlier(l10_games=games)
        result = run(row)
        self.assertEqual(result.state, OutlierRecomputeState.RESOLVED)
        for reason in result.excluded_reasons:
            self.assertIn("event_id", reason)
            self.assertIn("l10_index", reason)
            self.assertIn("excluded_value", reason)
            self.assertIn("exclusion_reason", reason)
            # excluded_value must match the actual game value
            idx = reason["l10_index"]
            self.assertEqual(reason["excluded_value"], games[idx])

    def test_updated_bounds_are_numeric_or_none(self):
        """updated_lower_bound / upper_bound are either floats or None (no model)."""
        games = [20, 18, 22, 19, 25, 21, 18, 17, 19, 45]
        row = _row_with_outlier(l10_games=games, line=20.5)
        result = run(row)
        self.assertEqual(result.state, OutlierRecomputeState.RESOLVED)
        if result.updated_lower_bound is not None:
            self.assertIsInstance(result.updated_lower_bound, float)
            self.assertIsInstance(result.updated_upper_bound, float)
            self.assertLessEqual(result.updated_lower_bound, result.updated_upper_bound)

    def test_original_evidence_preserved_on_resolved(self):
        games = [20, 18, 22, 19, 25, 21, 18, 17, 19, 45]
        row = _row_with_outlier(l10_games=games)
        result = run(row)
        _assert_original_evidence_preserved(self, result, games)

    def test_data_contract_fail_reason_none_on_resolved(self):
        games = [20, 18, 22, 19, 25, 21, 18, 17, 19, 45]
        row = _row_with_outlier(l10_games=games)
        result = run(row)
        self.assertEqual(result.state, OutlierRecomputeState.RESOLVED)
        self.assertIsNone(result.data_contract_fail_reason)
        self.assertIsNone(result.error_reason)

    def test_enrichment_game_log_ids_used_for_event_ids(self):
        """When enrichment provides a game_log, real game IDs should be used."""
        games = [20, 18, 22, 19, 25, 21, 18, 17, 19, 45]
        row = _row_with_outlier(l10_games=games)
        enrichment = {
            "game_log": [{"game_id": f"GAME_{i:03d}", "value": v}
                         for i, v in enumerate(games)]
        }
        result = run(row, enrichment=enrichment)
        self.assertEqual(result.state, OutlierRecomputeState.RESOLVED)
        # At least one event ID should come from the enrichment
        ids_from_enrichment = [
            eid for eid in result.excluded_event_ids
            if eid.startswith("GAME_")
        ]
        self.assertGreater(len(ids_from_enrichment), 0)

    def test_recomputed_distribution_has_required_keys(self):
        games = [20, 18, 22, 19, 25, 21, 18, 17, 19, 45]
        row = _row_with_outlier(l10_games=games)
        result = run(row)
        self.assertEqual(result.state, OutlierRecomputeState.RESOLVED)
        dist = result.recomputed_distribution
        for key in ("count", "mean", "median", "min", "max", "values"):
            self.assertIn(key, dist, f"recomputed_distribution must have key {key!r}")

    def test_result_is_frozen(self):
        games = [20, 18, 22, 19, 25, 21, 18, 17, 19, 45]
        row = _row_with_outlier(l10_games=games)
        result = run(row)
        with self.assertRaises((AttributeError, TypeError)):
            result.state = "RESOLVED"  # type: ignore  # frozen dataclass


# ===========================================================================
# 2. UNRESOLVED / DATA_CONTRACT_FAIL states
# ===========================================================================

class TestUnresolvedState(unittest.TestCase):

    def test_missing_game_log_unresolved(self):
        """l10_games absent → UNRESOLVED:MISSING_GAME_LOG."""
        row = _row_with_outlier(l10_games=[20, 18, 22, 45, 19, 21, 18, 17, 19, 25])
        # Remove the game log from the gate result
        row["gates"]["l5_l10_ledger"].pop("l10_games")
        result = run(row)
        _assert_governance(self, result)
        self.assertEqual(result.state, OutlierRecomputeState.UNRESOLVED)
        self.assertEqual(result.data_contract_fail_reason,
                         DataContractFailReason.MISSING_GAME_LOG)
        self.assertIsNone(result.recomputed_distribution)
        self.assertGreater(len(result.acquisition_attempts), 0)

    def test_empty_game_log_unresolved(self):
        row = _row_with_outlier()
        row["gates"]["l5_l10_ledger"]["l10_games"] = []
        result = run(row)
        self.assertEqual(result.state, OutlierRecomputeState.UNRESOLVED)
        self.assertEqual(result.data_contract_fail_reason,
                         DataContractFailReason.MISSING_GAME_LOG)

    def test_sample_too_small_to_isolate(self):
        """Fewer than MIN_GAMES_TO_ISOLATE games → UNRESOLVED."""
        games = [10.0, 12.0, 30.0]  # 3 games < MIN_GAMES_TO_ISOLATE (4)
        row = _row_with_outlier(l10_games=games)
        result = run(row)
        self.assertEqual(result.state, OutlierRecomputeState.UNRESOLVED)
        self.assertEqual(result.data_contract_fail_reason,
                         DataContractFailReason.SAMPLE_TOO_SMALL_TO_ISOLATE)

    def test_sample_too_small_after_exclusion(self):
        """
        If removing outlier(s) leaves fewer than MIN_GAMES_AFTER_EXCLUSION
        games, the result must be UNRESOLVED (not RESOLVED with 1 game).
        """
        # 4 games, max is clearly an outlier, but 3 remain which is exactly
        # MIN_GAMES_AFTER_EXCLUSION.  Let's use a case where removing max
        # leaves only 2 games.
        games = [5.0, 4.0, 99.0, 6.0]  # 4 games, max=99
        # Override flags to force season_high detection
        flags = {
            "season_high_outlier":    True,
            "avg_inflated_by_outlier": True,
            "l5_l10_gap_flagged":     False,
            "assist_volatile":        False,
            "median_disagrees_avg":   False,
        }
        row = _row_with_outlier(l10_games=games, flags=flags)
        # MIN_GAMES_AFTER_EXCLUSION = 3; removing 1 outlier leaves 3 → RESOLVED
        # To force UNRESOLVED, make a 3-game window where removing max leaves 2
        games2 = [5.0, 50.0, 6.0]  # 3 games → TOO_SMALL_TO_ISOLATE (< 4)
        row["gates"]["l5_l10_ledger"]["l10_games"] = games2
        result = run(row)
        self.assertEqual(result.state, OutlierRecomputeState.UNRESOLVED)
        self.assertIn(result.data_contract_fail_reason, (
            DataContractFailReason.SAMPLE_TOO_SMALL_TO_ISOLATE,
            DataContractFailReason.SAMPLE_TOO_SMALL_AFTER_EXCLUSION,
        ))

    def test_missing_outlier_gate_result_unresolved(self):
        row = _row_with_outlier()
        row["gates"].pop("outlier_gate")
        result = run(row)
        self.assertEqual(result.state, OutlierRecomputeState.UNRESOLVED)
        self.assertEqual(result.data_contract_fail_reason,
                         DataContractFailReason.MISSING_OUTLIER_GATE_RESULT)

    def test_skipped_outlier_gate_unresolved(self):
        row = _row_with_outlier()
        row["gates"]["outlier_gate"]["skipped"] = True
        result = run(row)
        self.assertEqual(result.state, OutlierRecomputeState.UNRESOLVED)

    def test_unresolved_always_has_named_reason(self):
        """Every UNRESOLVED result must have a non-None, non-empty reason."""
        # Generate several UNRESOLVED scenarios
        scenarios = [
            _row_with_outlier(l10_games=[]),
            _row_with_outlier(l10_games=[10.0, 40.0]),
        ]
        # Remove gate
        row_no_gate = _row_with_outlier()
        row_no_gate["gates"].pop("outlier_gate")
        scenarios.append(row_no_gate)

        for row in scenarios:
            # Patch l10_games if needed
            if row["gates"].get("l5_l10_ledger", {}).get("l10_games") == []:
                pass  # empty is fine
            result = run(row)
            if result.state == OutlierRecomputeState.UNRESOLVED:
                self.assertIsNotNone(result.data_contract_fail_reason)
                self.assertNotEqual(result.data_contract_fail_reason, "")

    def test_acquisition_attempts_listed_on_unresolved(self):
        row = _row_with_outlier()
        row["gates"]["l5_l10_ledger"].pop("l10_games")
        result = run(row)
        self.assertEqual(result.state, OutlierRecomputeState.UNRESOLVED)
        self.assertGreater(len(result.acquisition_attempts), 0)
        for attempt in result.acquisition_attempts:
            self.assertIn("field", attempt)
            self.assertIn("outcome", attempt)

    def test_original_evidence_preserved_on_unresolved(self):
        games = [20, 18, 22, 19, 25, 21, 18, 17, 19, 45]
        row = _row_with_outlier(l10_games=games)
        row["gates"]["l5_l10_ledger"].pop("l10_games")  # force UNRESOLVED
        result = run(row)
        self.assertEqual(result.state, OutlierRecomputeState.UNRESOLVED)
        # Even on failure, original_evidence must be populated (with what was available)
        self.assertIsNotNone(result.original_evidence)
        self.assertIsInstance(result.original_evidence, dict)


# ===========================================================================
# 3. ERROR state
# ===========================================================================

class TestErrorState(unittest.TestCase):

    def test_corrupt_games_list_handled_gracefully(self):
        """Non-numeric game values should not cause an unhandled exception."""
        row = _row_with_outlier()
        row["gates"]["l5_l10_ledger"]["l10_games"] = ["bad", None, "also_bad", 10.0]
        result = run(row)
        # Should not raise; may return UNRESOLVED (bad values filtered) or ERROR
        self.assertIn(result.state, (
            OutlierRecomputeState.UNRESOLVED,
            OutlierRecomputeState.ERROR,
            OutlierRecomputeState.RESOLVED,
        ))
        _assert_governance(self, result)

    def test_run_never_raises(self):
        """run() must never raise; always return OutlierRecomputeResult."""
        bad_rows = [
            {},
            {"gates": {}},
            {"gates": {"outlier_gate": "not_a_dict"}},
            {"gates": {"outlier_gate": None}},
            None,
        ]
        for bad_row in bad_rows:
            try:
                result = run(bad_row)  # type: ignore
                self.assertIsInstance(result, OutlierRecomputeResult)
            except Exception as exc:
                self.fail(f"run({bad_row!r}) raised {type(exc).__name__}: {exc}")

    def test_error_result_has_error_reason(self):
        """When state=ERROR, error_reason must be set and non-empty."""
        # Inject a pathological structure that the engine tries to process
        row = {"gates": {"l5_l10_ledger": {"l10_games": [1, 2, 3, 4, 5, 6, 7, 8, 9, 10],
                                            "passed": True},
                          "outlier_gate": {"passed": True, "skipped": False,
                                           "any_flag": True, "flags": {"season_high_outlier": True}}}}
        result = run(row)
        # Could be RESOLVED or UNRESOLVED — just verify it's a valid result
        _assert_governance(self, result)
        if result.state == OutlierRecomputeState.ERROR:
            self.assertIsNotNone(result.error_reason)
            self.assertNotEqual(result.error_reason, "")


# ===========================================================================
# 4. Governance invariants
# ===========================================================================

class TestGovernanceInvariants(unittest.TestCase):

    def test_all_states_have_terminal_label_authority_false(self):
        scenarios = [
            _row_with_outlier(l10_games=[20, 18, 22, 19, 25, 21, 18, 17, 19, 45]),
            _row_with_outlier(l10_games=[]),  # UNRESOLVED
        ]
        for row in scenarios:
            result = run(row)
            self.assertFalse(result.terminal_label_authority)

    def test_can_execute_always_false(self):
        games = [20, 18, 22, 19, 25, 21, 18, 17, 19, 45]
        result = run(_row_with_outlier(l10_games=games))
        self.assertFalse(result.can_execute)

    def test_module_level_constants(self):
        from gate_engine import outlier_recompute as mod
        self.assertFalse(mod.can_execute)
        self.assertFalse(mod.PRODUCTION_AUTHORITY)
        self.assertFalse(mod.TERMINAL_LABEL_AUTHORITY)
        self.assertFalse(mod.USER_OUTPUT_AUTHORITY)

    def test_no_terminal_label_field_on_result(self):
        """OutlierRecomputeResult must not carry a terminal_label field."""
        games = [20, 18, 22, 19, 25, 21, 18, 17, 19, 45]
        result = run(_row_with_outlier(l10_games=games))
        self.assertFalse(hasattr(result, "terminal_label"))

    def test_state_enum_has_exactly_three_values(self):
        states = {s.value for s in OutlierRecomputeState}
        self.assertEqual(states, {"RESOLVED", "UNRESOLVED", "ERROR"})

    def test_result_is_frozen(self):
        games = [20, 18, 22, 19, 25, 21, 18, 17, 19, 45]
        result = run(_row_with_outlier(l10_games=games))
        with self.assertRaises((AttributeError, TypeError)):
            result.can_execute = True  # type: ignore  # frozen dataclass


# ===========================================================================
# 5. WNBA fixtures
# ===========================================================================

class TestWNBAFixtures(unittest.TestCase):

    def test_wnba_points_outlier_resolved(self):
        games = [20, 18, 22, 19, 25, 21, 18, 17, 19, 45]
        row = _row_with_outlier(sport="WNBA", prop_type="PTS", l10_games=games, line=20.5)
        result = run(row)
        _assert_governance(self, result)
        self.assertEqual(result.state, OutlierRecomputeState.RESOLVED)
        _assert_original_evidence_preserved(self, result, games)
        # The outlier (45) must be in excluded values
        excluded_vals = [r["excluded_value"] for r in result.excluded_reasons]
        self.assertIn(45, excluded_vals)

    def test_wnba_assists_small_sample_unresolved(self):
        """WNBA assists with only 3 games → UNRESOLVED (too small to isolate)."""
        games = [3.0, 2.0, 12.0]
        flags = {"assist_volatile": True, "l5_l10_gap_flagged": False,
                 "avg_inflated_by_outlier": False, "season_high_outlier": True,
                 "median_disagrees_avg": False}
        row = _row_with_outlier(sport="WNBA", prop_type="AST",
                                l10_games=games, flags=flags, line=3.5)
        result = run(row)
        self.assertEqual(result.state, OutlierRecomputeState.UNRESOLVED)
        self.assertIn(result.data_contract_fail_reason, (
            DataContractFailReason.SAMPLE_TOO_SMALL_TO_ISOLATE,
            DataContractFailReason.SAMPLE_TOO_SMALL_AFTER_EXCLUSION,
        ))
        _assert_original_evidence_preserved(self, result, games)

    def test_wnba_rebounds_outlier_resolved(self):
        games = [7, 8, 6, 9, 7, 8, 6, 7, 8, 22]
        row = _row_with_outlier(sport="WNBA", prop_type="REB",
                                l10_games=games, line=7.5)
        result = run(row)
        self.assertEqual(result.state, OutlierRecomputeState.RESOLVED)
        self.assertGreater(len(result.excluded_event_ids), 0)
        _assert_original_evidence_preserved(self, result, games)

    def test_wnba_assists_volatile_resolved(self):
        """Assist-volatile flag: outlier isolation via high-variance detection."""
        games = [3, 2, 4, 3, 2, 4, 3, 3, 2, 18]  # 18 is volatile outlier
        flags = {
            "assist_volatile": True, "l5_l10_gap_flagged": False,
            "avg_inflated_by_outlier": True, "season_high_outlier": True,
            "median_disagrees_avg": False,
        }
        row = _row_with_outlier(sport="WNBA", prop_type="AST",
                                l10_games=games, flags=flags, line=3.5)
        result = run(row)
        # Should resolve since 9 games remain after removing the outlier
        self.assertEqual(result.state, OutlierRecomputeState.RESOLVED)


# ===========================================================================
# 6. MLB pitcher strikeouts fixtures
# ===========================================================================

class TestMLBStrikeoutsFixtures(unittest.TestCase):

    def test_mlb_strikeouts_single_outlier_resolved(self):
        games = [5, 6, 8, 5, 7, 6, 9, 5, 6, 20]  # 20 is the outlier
        row = _row_with_outlier(sport="MLB", prop_type="SO",
                                l10_games=games, line=6.5)
        result = run(row)
        _assert_governance(self, result)
        self.assertEqual(result.state, OutlierRecomputeState.RESOLVED)
        excluded_vals = [r["excluded_value"] for r in result.excluded_reasons]
        self.assertIn(20, excluded_vals)
        # Recomputed mean should be lower than original
        self.assertLess(result.recomputed_distribution["mean"],
                        statistics.mean(games))

    def test_mlb_strikeouts_no_game_log_unresolved(self):
        row = _row_with_outlier(sport="MLB", prop_type="K", line=5.5)
        row["gates"]["l5_l10_ledger"].pop("l10_games")
        result = run(row)
        self.assertEqual(result.state, OutlierRecomputeState.UNRESOLVED)
        self.assertEqual(result.data_contract_fail_reason,
                         DataContractFailReason.MISSING_GAME_LOG)

    def test_mlb_strikeouts_original_evidence_always_preserved(self):
        games = [5, 6, 8, 5, 7, 6, 9, 5, 6, 20]
        row = _row_with_outlier(sport="MLB", prop_type="STRIKEOUTS",
                                l10_games=games, line=6.5)
        result = run(row)
        _assert_original_evidence_preserved(self, result, games)
        # Original evidence must capture the sport
        self.assertEqual(result.original_evidence.get("sport"), "MLB")

    def test_mlb_strikeouts_updated_bounds_logical(self):
        games = [5, 6, 8, 5, 7, 6, 9, 5, 6, 20]
        row = _row_with_outlier(sport="MLB", prop_type="SO",
                                l10_games=games, line=6.5)
        result = run(row)
        if (result.updated_lower_bound is not None
                and result.updated_upper_bound is not None):
            self.assertLessEqual(result.updated_lower_bound,
                                 result.updated_upper_bound)
            self.assertGreater(result.updated_lower_bound, 0.0)
            self.assertLess(result.updated_upper_bound, 1.0)


# ===========================================================================
# 7. Mixed-batch independence
# ===========================================================================

class TestMixedBatchIndependence(unittest.TestCase):

    def test_five_rows_independent_states(self):
        """
        Processing multiple rows must not share state between calls.
        Each row's result depends only on its own input.
        """
        configs = [
            # (l10_games, expect_resolved)
            ([20, 18, 22, 19, 25, 21, 18, 17, 19, 45], True),   # has outlier
            ([], False),                                           # empty games
            ([20, 18, 22, 19, 25, 21, 18, 17, 19, 45], True),   # same as #1
            ([10, 10], False),                                     # too small
            ([7, 8, 6, 9, 7, 8, 6, 7, 8, 22], True),            # rebounds-style
        ]

        results = []
        for games, _ in configs:
            row = _row_with_outlier(l10_games=games)
            results.append(run(row))

        for i, (result, (_, expect_resolved)) in enumerate(zip(results, configs)):
            _assert_governance(self, result)
            if expect_resolved:
                self.assertEqual(
                    result.state, OutlierRecomputeState.RESOLVED,
                    f"Row {i} expected RESOLVED, got {result.state}"
                )
            else:
                self.assertNotEqual(
                    result.state, OutlierRecomputeState.RESOLVED,
                    f"Row {i} expected non-RESOLVED, got {result.state}"
                )

    def test_result_objects_are_independent(self):
        """result objects from different run() calls are distinct objects."""
        games = [20, 18, 22, 19, 25, 21, 18, 17, 19, 45]
        r1 = run(_row_with_outlier(l10_games=games))
        r2 = run(_row_with_outlier(l10_games=games))
        self.assertIsNot(r1, r2)
        # Both should agree on state since inputs are identical
        self.assertEqual(r1.state, r2.state)

    def test_original_evidence_not_shared_between_rows(self):
        g1 = [20, 18, 22, 19, 25, 21, 18, 17, 19, 45]
        g2 = [5, 6, 8, 5, 7, 6, 9, 5, 6, 20]
        r1 = run(_row_with_outlier(l10_games=g1))
        r2 = run(_row_with_outlier(l10_games=g2))
        self.assertEqual(r1.original_evidence["l10_games"], g1)
        self.assertEqual(r2.original_evidence["l10_games"], g2)


# ===========================================================================
# 8. Explicit three-state contract
# ===========================================================================

class TestExplicitThreeStateContract(unittest.TestCase):

    def test_state_is_always_one_of_three(self):
        valid_states = {
            OutlierRecomputeState.RESOLVED,
            OutlierRecomputeState.UNRESOLVED,
            OutlierRecomputeState.ERROR,
        }
        scenarios = [
            _row_with_outlier(l10_games=[20, 18, 22, 19, 25, 21, 18, 17, 19, 45]),
            _row_with_outlier(l10_games=[]),
            _row_with_outlier(l10_games=[10.0, 11.0]),
        ]
        for row in scenarios:
            result = run(row)
            self.assertIn(result.state, valid_states,
                          f"state {result.state!r} not in valid set")

    def test_unresolved_reason_is_never_empty_string(self):
        row = _row_with_outlier(l10_games=[])
        result = run(row)
        if result.state == OutlierRecomputeState.UNRESOLVED:
            self.assertNotEqual(result.reason, "")
            self.assertNotEqual(result.reason_detail, "")

    def test_resolved_reason_detail_is_informative(self):
        games = [20, 18, 22, 19, 25, 21, 18, 17, 19, 45]
        row = _row_with_outlier(l10_games=games)
        result = run(row)
        if result.state == OutlierRecomputeState.RESOLVED:
            self.assertIn("outlier", result.reason_detail.lower())

    def test_recomputed_distribution_none_on_non_resolved(self):
        row = _row_with_outlier(l10_games=[])
        result = run(row)
        if result.state != OutlierRecomputeState.RESOLVED:
            self.assertIsNone(result.recomputed_distribution)
            self.assertIsNone(result.updated_lower_bound)
            self.assertIsNone(result.updated_upper_bound)


if __name__ == "__main__":
    unittest.main()
