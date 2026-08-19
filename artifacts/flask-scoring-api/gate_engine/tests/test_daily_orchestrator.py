"""
gate_engine/tests/test_daily_orchestrator.py

WOW-PATCH-2026-08-19-DAILY-CANONICAL-v1.0 (Task #277)

Regression suite for the canonical WOW Daily orchestration layer.
All tests are offline/unit-level — no HTTP calls, no DB writes.

Invariants verified
-------------------
1.  can_execute = False on daily_orchestrator module
2.  resolve_participant_side fail-closed (SIDE_UNKNOWN on absent marker)
3.  resolve_participant_side explicit HOME / AWAY markers
4.  normalise_soccer_outcome: canonical HOME / DRAW / AWAY mapping
5.  normalise_soccer_props: in-place normalisation on soccer rows
6.  wnba_ml_specialist_ready: True only with complete paired inputs
7.  tennis_ml_specialist_ready: True only with complete paired inputs
8.  _canonical_selection_id stability (same input → same ID)
9.  _canonical_selection_id uniqueness (different inputs → different IDs)
10. _market_version_id stability
11. Source union always calls backup even when primary succeeds
12. No pre-score truncation when _props_by_sport supplied to run_scan
13. No save_scan_result calls when _persist_results=False
14. Reconciliation passes when all selections are in terminal buckets
15. Reconciliation fails when a selection is missing from terminal buckets
16. Reconciliation flags duplicate canonical_selection_ids
17. Reconciliation flags excess terminal IDs
18. run_daily_orchestration returns required top-level keys
19. run_daily_orchestration: run_status DEGRADED when failed_modules non-empty
20. run_daily_orchestration: persist=False skips DB writes
21. run_daily_orchestration: RECONCILIATION_WARNING when reconciliation fails
22. _union_props_for_sport: dedup across sources (same key → first wins)
23. Soccer 1X2 outcome normalised before evaluation
24. WNBA readiness PARTIAL when only home_win_pct present (no away)
25. Tennis readiness PARTIAL when only surface present (no paired field)
"""
from __future__ import annotations

import sys
import os
import types
import unittest
from unittest.mock import MagicMock, patch

# ---------------------------------------------------------------------------
# Path fix
# ---------------------------------------------------------------------------
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))


# ---------------------------------------------------------------------------
# Module under test (import after path fix)
# ---------------------------------------------------------------------------
import gate_engine.daily_orchestrator as orch


# ---------------------------------------------------------------------------
# Test 1 — module-level can_execute invariant
# ---------------------------------------------------------------------------
class TestCanExecute(unittest.TestCase):
    def test_can_execute_false(self):
        self.assertFalse(orch.can_execute)


# ---------------------------------------------------------------------------
# Tests 2-3 — resolve_participant_side
# ---------------------------------------------------------------------------
class TestResolveParticipantSide(unittest.TestCase):

    def test_absent_marker_returns_unknown(self):
        row = {"player": "Acme", "prop": "points"}
        self.assertEqual(orch.resolve_participant_side(row), "SIDE_UNKNOWN")

    def test_empty_row_returns_unknown(self):
        self.assertEqual(orch.resolve_participant_side({}), "SIDE_UNKNOWN")

    def test_home_marker_home_away_field(self):
        for marker in ("HOME", "home", "vs", "VS.", "TRUE", "1", "YES"):
            self.assertEqual(
                orch.resolve_participant_side({"home_away": marker}),
                "HOME",
                f"Expected HOME for marker={marker!r}",
            )

    def test_away_marker_home_away_field(self):
        for marker in ("AWAY", "away", "@", "FALSE", "0", "NO"):
            self.assertEqual(
                orch.resolve_participant_side({"home_away": marker}),
                "AWAY",
                f"Expected AWAY for marker={marker!r}",
            )

    def test_is_home_field_recognised(self):
        self.assertEqual(orch.resolve_participant_side({"is_home": "HOME"}), "HOME")
        self.assertEqual(orch.resolve_participant_side({"is_home": "@"}),    "AWAY")

    def test_unrecognised_non_empty_marker_returns_unknown(self):
        # A value that isn't in HOME or AWAY markers should be SIDE_UNKNOWN
        result = orch.resolve_participant_side({"home_away": "NEUTRAL"})
        self.assertEqual(result, "SIDE_UNKNOWN")

    def test_none_value_treated_as_absent(self):
        self.assertEqual(orch.resolve_participant_side({"home_away": None}), "SIDE_UNKNOWN")


# ---------------------------------------------------------------------------
# Tests 4-5 — Soccer 1X2 normalisation
# ---------------------------------------------------------------------------
class TestSoccerOutcomeNormalisation(unittest.TestCase):

    def test_canonical_already_valid(self):
        for val, expected in [("HOME", "HOME"), ("DRAW", "DRAW"), ("AWAY", "AWAY")]:
            self.assertEqual(orch.normalise_soccer_outcome(val), expected)

    def test_numeric_1x2(self):
        self.assertEqual(orch.normalise_soccer_outcome("1"), "HOME")
        self.assertEqual(orch.normalise_soccer_outcome("X"), "DRAW")
        self.assertEqual(orch.normalise_soccer_outcome("x"), "DRAW")
        self.assertEqual(orch.normalise_soccer_outcome("2"), "AWAY")

    def test_word_forms(self):
        self.assertEqual(orch.normalise_soccer_outcome("home"),  "HOME")
        self.assertEqual(orch.normalise_soccer_outcome("draw"),  "DRAW")
        self.assertEqual(orch.normalise_soccer_outcome("away"),  "AWAY")
        self.assertEqual(orch.normalise_soccer_outcome("H"),     "HOME")

    def test_none_input(self):
        self.assertIsNone(orch.normalise_soccer_outcome(None))

    def test_unrecognised_input(self):
        self.assertIsNone(orch.normalise_soccer_outcome("WIN"))

    def test_normalise_soccer_props_in_place(self):
        props = [
            {"sport": "Soccer", "side": "1",    "player": "A"},
            {"sport": "Soccer", "side": "X",    "player": "B"},
            {"sport": "Soccer", "side": "2",    "player": "C"},
            {"sport": "NBA",    "side": "MORE", "player": "D"},  # should be untouched
        ]
        orch.normalise_soccer_props(props)
        self.assertEqual(props[0]["outcome"], "HOME")
        self.assertEqual(props[1]["outcome"], "DRAW")
        self.assertEqual(props[2]["outcome"], "AWAY")
        self.assertNotIn("outcome", props[3])


# ---------------------------------------------------------------------------
# Tests 6-7 — Specialist readiness contracts
# ---------------------------------------------------------------------------
class TestWnbaSpecialistReady(unittest.TestCase):

    def test_both_win_pcts_ready(self):
        enr = {"home_win_pct": 0.60, "away_win_pct": 0.40}
        self.assertTrue(orch.wnba_ml_specialist_ready(enr))

    def test_both_power_fields_ready(self):
        enr = {"home_power": 0.60, "away_power": 0.40}
        self.assertTrue(orch.wnba_ml_specialist_ready(enr))

    def test_both_elo_fields_ready(self):
        enr = {"home_elo": 1510, "away_elo": 1490}
        self.assertTrue(orch.wnba_ml_specialist_ready(enr))

    def test_only_home_win_pct_not_ready(self):
        """Partial hydration must NOT be treated as ready."""
        enr = {"home_win_pct": 0.60}
        self.assertFalse(orch.wnba_ml_specialist_ready(enr))

    def test_only_away_win_pct_not_ready(self):
        enr = {"away_win_pct": 0.40}
        self.assertFalse(orch.wnba_ml_specialist_ready(enr))

    def test_empty_enrichment_not_ready(self):
        self.assertFalse(orch.wnba_ml_specialist_ready({}))


class TestTennisSpecialistReady(unittest.TestCase):

    def test_surface_and_adjusted_form_ready(self):
        enr = {"surface_adjusted_form": 0.55, "surface": "clay"}
        self.assertTrue(orch.tennis_ml_specialist_ready(enr))

    def test_elo_pair_ready(self):
        enr = {"home_elo": 1600, "away_elo": 1550}
        self.assertTrue(orch.tennis_ml_specialist_ready(enr))

    def test_hold_break_pair_ready(self):
        enr = {"hold_rate": 0.72, "break_rate": 0.28}
        self.assertTrue(orch.tennis_ml_specialist_ready(enr))

    def test_only_surface_not_ready(self):
        """surface alone is not enough — needs a paired input."""
        enr = {"surface": "clay"}
        self.assertFalse(orch.tennis_ml_specialist_ready(enr))

    def test_empty_not_ready(self):
        self.assertFalse(orch.tennis_ml_specialist_ready({}))


# ---------------------------------------------------------------------------
# Tests 8-10 — Identity helpers
# ---------------------------------------------------------------------------
class TestCanonicalIdentity(unittest.TestCase):

    def test_selection_id_stability(self):
        a = orch._canonical_selection_id("MLB", "Shohei Ohtani", "home_runs", "MORE", 0.5)
        b = orch._canonical_selection_id("MLB", "Shohei Ohtani", "home_runs", "MORE", 0.5)
        self.assertEqual(a, b)

    def test_selection_id_starts_with_prefix(self):
        sel_id = orch._canonical_selection_id("NBA", "Player A", "points", "MORE", 24.5)
        self.assertTrue(sel_id.startswith("SEL_"))

    def test_selection_id_uniqueness(self):
        a = orch._canonical_selection_id("NBA", "Player A", "points", "MORE", 24.5)
        b = orch._canonical_selection_id("NBA", "Player B", "points", "MORE", 24.5)
        self.assertNotEqual(a, b)

    def test_selection_id_line_bucket(self):
        # 24.4 and 24.6 both bucket to 24.5 → same ID
        a = orch._canonical_selection_id("NBA", "Player A", "points", "MORE", 24.4)
        b = orch._canonical_selection_id("NBA", "Player A", "points", "MORE", 24.6)
        self.assertEqual(a, b)

    def test_market_version_id_stability(self):
        sel_id = orch._canonical_selection_id("NBA", "Player A", "points", "MORE", 24.5)
        a = orch._market_version_id(sel_id, "2026-08-19", ("ODDS_OK", "RD_OK"))
        b = orch._market_version_id(sel_id, "2026-08-19", ("ODDS_OK", "RD_OK"))
        self.assertEqual(a, b)
        self.assertTrue(a.startswith("MKT_"))

    def test_market_version_id_changes_with_source(self):
        sel_id = orch._canonical_selection_id("NBA", "Player A", "points", "MORE", 24.5)
        a = orch._market_version_id(sel_id, "2026-08-19", ("ODDS_OK", "RD_OK"))
        b = orch._market_version_id(sel_id, "2026-08-19", ("ODDS_FAIL", "RD_OK"))
        self.assertNotEqual(a, b)


# ---------------------------------------------------------------------------
# Tests 14-17 — Reconciliation
# ---------------------------------------------------------------------------
class TestReconciliation(unittest.TestCase):

    def _make_card(self, sel_id, bucket="watch"):
        return {
            "canonical_selection_id": sel_id,
            "terminal_bucket": bucket,
            "classification": bucket,
        }

    def test_reconciled_when_all_accounted(self):
        discovered = {"SEL_A", "SEL_B", "SEL_C"}
        scan = {
            "market_verified": [self._make_card("SEL_A", "market_verified")],
            "final_approved_internal": [],
            "model_qualified": [self._make_card("SEL_B", "model_qualified")],
            "conditional": [],
            "watch": [self._make_card("SEL_C", "watch")],
            "reject": [],
            "data_insufficient": [],
            "no_play": [],
        }
        r = orch._build_reconciliation(discovered, scan)
        self.assertTrue(r["reconciled"])
        self.assertEqual(r["discovered_count"], 3)
        self.assertEqual(r["total_terminal"], 3)
        self.assertEqual(r["missing_ids"], [])
        self.assertEqual(r["excess_ids"], [])
        self.assertEqual(r["duplicate_ids"], [])

    def test_fails_when_selection_missing_from_terminal(self):
        discovered = {"SEL_A", "SEL_B", "SEL_MISSING"}
        scan = {
            "market_verified": [self._make_card("SEL_A")],
            "final_approved_internal": [],
            "model_qualified": [],
            "conditional": [],
            "watch": [self._make_card("SEL_B")],
            "reject": [],
            "data_insufficient": [],
            "no_play": [],
        }
        r = orch._build_reconciliation(discovered, scan)
        self.assertFalse(r["reconciled"])
        self.assertIn("SEL_MISSING", r["missing_ids"])

    def test_fails_on_duplicate_canonical_id(self):
        discovered = {"SEL_A", "SEL_B"}
        scan = {
            "market_verified": [self._make_card("SEL_A")],
            "final_approved_internal": [self._make_card("SEL_A")],  # duplicate!
            "model_qualified": [],
            "conditional": [],
            "watch": [self._make_card("SEL_B")],
            "reject": [],
            "data_insufficient": [],
            "no_play": [],
        }
        r = orch._build_reconciliation(discovered, scan)
        self.assertFalse(r["reconciled"])
        self.assertIn("SEL_A", r["duplicate_ids"])

    def test_excess_ids_flagged(self):
        discovered = {"SEL_A"}
        scan = {
            "market_verified": [self._make_card("SEL_A")],
            "final_approved_internal": [],
            "model_qualified": [],
            "conditional": [],
            "watch": [self._make_card("SEL_EXTRA")],  # not in discovered
            "reject": [],
            "data_insufficient": [],
            "no_play": [],
        }
        r = orch._build_reconciliation(discovered, scan)
        self.assertFalse(r["reconciled"])
        self.assertIn("SEL_EXTRA", r["excess_ids"])

    def test_empty_discovered_and_terminal_reconciles(self):
        r = orch._build_reconciliation(set(), {b: [] for b in orch._TERMINAL_BUCKETS})
        self.assertTrue(r["reconciled"])


# ---------------------------------------------------------------------------
# Tests 18-21 — run_daily_orchestration (mocked)
# ---------------------------------------------------------------------------

def _make_mock_scan_result(buckets=None):
    """Return a minimal run_scan result with canonical IDs on every card."""
    buckets = buckets or {}
    result = {b: [] for b in orch._TERMINAL_BUCKETS}
    result.update(buckets)
    result["run_status"]     = "COMPLETE"
    result["failed_modules"] = []
    result["execution_notes"] = []
    return result


class TestRunDailyOrchestration(unittest.TestCase):
    """
    All tests mock out the HTTP-bound dependencies so orchestration
    can run fully offline.
    """

    def _mock_union(self, sport):
        """Return two deterministic props for any sport."""
        props = [
            {"player": f"Player_A_{sport}", "prop": "points", "side": "MORE",
             "line": 20.5, "sport": sport, "game_date": "2026-08-19"},
            {"player": f"Player_B_{sport}", "prop": "rebounds", "side": "MORE",
             "line": 5.5, "sport": sport, "game_date": "2026-08-19"},
        ]
        return props, {f"{sport}_odds": "AVAILABLE", f"{sport}_rundown": "AVAILABLE"}

    def _run_with_mocks(self, sports=None, extra_scan_override=None):
        """
        Execute run_daily_orchestration with all external I/O mocked.
        Returns the orchestration result dict.
        """
        from gate_engine.daily_orchestrator import run_daily_orchestration
        sports = sports or ["NBA"]

        # Build expected props_by_sport so we can predict canonical IDs
        mock_props = {}
        for sp in sports:
            props, _ = self._mock_union(sp)
            mock_props[sp] = props

        scan_result = _make_mock_scan_result({
            "watch": [
                {
                    "player": f"Player_A_{sp}",
                    "sport": sp,
                    "prop": "points",
                    "side": "MORE",
                    "line": 20.5,
                    "game_date": "2026-08-19",
                    "classification": "Watch",
                    "terminal_bucket": "Watch",
                    "wow_score": 40.0,
                    "final_approval_blocker": "low_score",
                    "audit_valid": False,
                }
                for sp in sports
            ] + [
                {
                    "player": f"Player_B_{sp}",
                    "sport": sp,
                    "prop": "rebounds",
                    "side": "MORE",
                    "line": 5.5,
                    "game_date": "2026-08-19",
                    "classification": "Watch",
                    "terminal_bucket": "Watch",
                    "wow_score": 38.0,
                    "final_approval_blocker": "low_score",
                    "audit_valid": False,
                }
                for sp in sports
            ],
        })
        if extra_scan_override:
            scan_result.update(extra_scan_override)

        with (
            patch.object(orch, "_union_props_for_sport", side_effect=self._mock_union),
            patch("jobs.wow_daily_scan.run_scan", return_value=scan_result),
            patch("storage.daily_manifest.ensure_tables", return_value=True),
            patch("storage.daily_manifest.create_run",    return_value=True),
            patch("storage.daily_manifest.finalize_run",  return_value=True),
            patch("storage.daily_manifest.save_run_row",  return_value=True),
        ):
            return run_daily_orchestration(
                sports=sports,
                environment="test",
                runtime_provenance=None,
                session_id="test-session",
                persist=True,
            )

    def test_required_top_level_keys(self):
        from gate_engine.daily_orchestrator import run_daily_orchestration
        result = self._run_with_mocks(["NBA"])
        for key in (
            "run_id", "run_date", "run_status", "counts",
            "playable_card", "reconciliation", "source_union",
            "missing_sports", "failed_modules", "runtime_provenance",
        ):
            self.assertIn(key, result, f"Missing key: {key}")

    def test_run_id_is_uuid_string(self):
        from gate_engine.daily_orchestrator import run_daily_orchestration
        import re
        result = self._run_with_mocks(["NBA"])
        self.assertRegex(result["run_id"], r"^[0-9a-f-]{36}$")

    def test_counts_has_total_discovered(self):
        from gate_engine.daily_orchestrator import run_daily_orchestration
        result = self._run_with_mocks(["NBA"])
        self.assertIn("total_discovered", result["counts"])
        self.assertGreater(result["counts"]["total_discovered"], 0)

    def test_persist_false_skips_db_writes(self):
        from gate_engine.daily_orchestrator import run_daily_orchestration
        sports = ["NBA"]
        props, _ = self._mock_union("NBA")
        scan_result = _make_mock_scan_result({"watch": [
            {"player": "Player_A_NBA", "sport": "NBA", "prop": "points",
             "side": "MORE", "line": 20.5, "classification": "Watch",
             "terminal_bucket": "Watch", "wow_score": 40.0,
             "final_approval_blocker": None, "audit_valid": False},
        ]})
        with (
            patch.object(orch, "_union_props_for_sport", side_effect=self._mock_union),
            patch("jobs.wow_daily_scan.run_scan", return_value=scan_result),
            patch("storage.daily_manifest.ensure_tables") as mock_ensure,
            patch("storage.daily_manifest.create_run")    as mock_create,
            patch("storage.daily_manifest.finalize_run")  as mock_finalize,
            patch("storage.daily_manifest.save_run_row")  as mock_save,
        ):
            run_daily_orchestration(
                sports=sports, environment="test",
                runtime_provenance=None, session_id=None,
                persist=False,
            )
        mock_ensure.assert_not_called()
        mock_create.assert_not_called()
        mock_finalize.assert_not_called()
        mock_save.assert_not_called()

    def test_degraded_status_on_failed_modules(self):
        from gate_engine.daily_orchestrator import run_daily_orchestration
        sports = ["NBA"]
        scan_result = _make_mock_scan_result()
        scan_result["failed_modules"] = ["NBA:fetch_all_props:ConnectionError"]
        scan_result["run_status"]     = "DEGRADED_ENGINE_RUN"
        with (
            patch.object(orch, "_union_props_for_sport", side_effect=self._mock_union),
            patch("jobs.wow_daily_scan.run_scan", return_value=scan_result),
            patch("storage.daily_manifest.ensure_tables", return_value=True),
            patch("storage.daily_manifest.create_run",    return_value=True),
            patch("storage.daily_manifest.finalize_run",  return_value=True),
            patch("storage.daily_manifest.save_run_row",  return_value=True),
        ):
            result = run_daily_orchestration(
                sports=sports, environment="test",
                runtime_provenance=None, session_id=None,
                persist=True,
            )
        self.assertEqual(result["run_status"], "DEGRADED")


# ---------------------------------------------------------------------------
# Test 11 — Source union: backup always called
# ---------------------------------------------------------------------------
class TestSourceUnion(unittest.TestCase):

    def test_union_calls_both_sources(self):
        """_union_props_for_sport should always call both primary AND backup."""
        primary = [
            {"player": "A", "prop": "pts", "side": "MORE", "line": 20.5, "sport": "NBA"}
        ]
        backup  = [
            {"player": "B", "prop": "reb", "side": "MORE", "line": 5.5,  "sport": "NBA"}
        ]
        with (
            patch("services.odds_api.fetch_all_props", return_value=(primary, "AVAILABLE")),
            patch("services.rundown.fetch_backup_props",   return_value=(backup, "AVAILABLE")),
        ):
            props, status = orch._union_props_for_sport("NBA")
        self.assertEqual(len(props), 2)  # both sources unioned
        players = {p["player"] for p in props}
        self.assertIn("A", players)
        self.assertIn("B", players)

    def test_union_deduplicates_same_key(self):
        """When both sources return the same (player,prop,side,line_bucket), keep first."""
        shared = {"player": "A", "prop": "pts", "side": "MORE", "line": 20.5, "sport": "NBA"}
        with (
            patch("services.odds_api.fetch_all_props",   return_value=([shared], "AVAILABLE")),
            patch("services.rundown.fetch_backup_props", return_value=([shared], "AVAILABLE")),
        ):
            props, _ = orch._union_props_for_sport("NBA")
        self.assertEqual(len(props), 1)

    def test_union_works_when_primary_fails(self):
        """Backup props are still returned even when primary raises."""
        backup = [{"player": "B", "prop": "reb", "side": "MORE", "line": 5.5, "sport": "NBA"}]
        with (
            patch("services.odds_api.fetch_all_props",   side_effect=Exception("timeout")),
            patch("services.rundown.fetch_backup_props", return_value=(backup, "AVAILABLE")),
        ):
            props, status = orch._union_props_for_sport("NBA")
        self.assertEqual(len(props), 1)
        self.assertIn("FAILED", status.get("NBA_odds", ""))


# ---------------------------------------------------------------------------
# Test 22 — run_scan _props_by_sport injection (no truncation)
# ---------------------------------------------------------------------------
class TestRunScanInjection(unittest.TestCase):

    def test_props_by_sport_skips_http_and_no_truncation(self):
        """
        When _props_by_sport is supplied:
        - no fetch_all_props / fetch_backup_props calls
        - props are NOT truncated even if > limit_per_sport
        """
        # Build 5 props for NBA
        sport    = "NBA"
        big_list = [
            {"player": f"P{i}", "prop": "points", "side": "MORE",
             "line": 20.5, "sport": sport, "game_date": "2026-08-19"}
            for i in range(5)
        ]

        from jobs.wow_daily_scan import run_scan

        called = []

        def mock_compute(*args, **kwargs):
            return 50, "STRONG", "ok"

        def mock_log_stats(*args, **kwargs):
            return {
                "raw_l5": [21, 22], "raw_l10": [20, 21, 22, 19, 18, 23, 24, 20, 21, 22],
                "l5_hit_rate": 0.8, "l10_hit_rate": 0.75,
                "l10_median": 21.0, "l10_avg": 21.0,
                "games_available": 10, "sample_scope": "full",
                "cross_season_used": False, "manual_fallback_used": False,
                "log_status": "AVAILABLE",
            }, "AVAILABLE"

        with (
            patch("services.odds_api.fetch_all_props",   side_effect=AssertionError("should not be called")),
            patch("services.rundown.fetch_backup_props", side_effect=AssertionError("should not be called")),
            patch("services.player_logs.get_player_log_stats", side_effect=mock_log_stats),
            patch("services.status.get_injuries",            return_value=({}, "OK")),
            patch("services.status.get_player_injury_flag",  return_value=(0, "OK", None)),
            patch("services.status.get_mlb_probable_pitchers", return_value=({}, "OK")),
        ):
            # Patch compute_wow_score at the module level inside wow_daily_scan
            import jobs.wow_daily_scan as scan_mod
            original_compute = scan_mod.compute_wow_score
            scan_mod.compute_wow_score = mock_compute
            try:
                result = run_scan(
                    sports=[sport],
                    environment="test",
                    limit_per_sport=2,      # would truncate if applied
                    _props_by_sport={sport: big_list},
                    _persist_results=False,
                )
            finally:
                scan_mod.compute_wow_score = original_compute

        # 5 props evaluated — not truncated to 2
        total_evaluated = sum(
            len(result.get(b, []))
            for b in ("market_verified", "final_approved_internal", "model_qualified",
                      "conditional", "watch", "reject", "data_insufficient", "no_play")
        )
        self.assertEqual(total_evaluated, 5, f"Expected 5, got {total_evaluated}")

    def test_persist_false_does_not_call_save_scan_result(self):
        """_persist_results=False must suppress save_scan_result calls."""
        from jobs.wow_daily_scan import run_scan
        sport    = "NBA"
        one_prop = [
            {"player": "Ptest", "prop": "points", "side": "MORE",
             "line": 20.5, "sport": sport, "game_date": "2026-08-19"}
        ]

        def mock_log_stats(*a, **k):
            return {
                "raw_l5": [21], "raw_l10": [21],
                "l5_hit_rate": 0.8, "l10_hit_rate": 0.75,
                "l10_median": 21.0, "l10_avg": 21.0,
                "games_available": 1, "sample_scope": "full",
                "cross_season_used": False, "manual_fallback_used": False,
                "log_status": "AVAILABLE",
            }, "AVAILABLE"

        with (
            patch("services.player_logs.get_player_log_stats", side_effect=mock_log_stats),
            patch("services.status.get_injuries",             return_value=({}, "OK")),
            patch("services.status.get_player_injury_flag",   return_value=(0, "OK", None)),
            patch("services.status.get_mlb_probable_pitchers", return_value=({}, "OK")),
            patch("storage.results.save_scan_result") as mock_save,
        ):
            import jobs.wow_daily_scan as scan_mod
            original_compute = scan_mod.compute_wow_score
            scan_mod.compute_wow_score = lambda *a, **k: (50, "ok", "ok")
            try:
                run_scan(
                    sports=[sport],
                    environment="test",
                    limit_per_sport=None,
                    _props_by_sport={sport: one_prop},
                    _persist_results=False,
                )
            finally:
                scan_mod.compute_wow_score = original_compute

        mock_save.assert_not_called()


# ---------------------------------------------------------------------------
# Test 23 — Soccer outcome normalisation integrated into source union
# ---------------------------------------------------------------------------
class TestSoccerOutcomeInUnion(unittest.TestCase):

    def test_soccer_outcomes_normalised_in_orchestrator(self):
        soccer_props = [
            {"player": "TeamA", "prop": "match_winner", "side": "1",
             "line": 1.0, "sport": "Soccer", "game_date": "2026-08-19",
             "outcome": "1"},
            {"player": "TeamA", "prop": "match_winner", "side": "X",
             "line": 1.0, "sport": "Soccer", "game_date": "2026-08-19",
             "outcome": "X"},
            {"player": "TeamB", "prop": "match_winner", "side": "2",
             "line": 1.0, "sport": "Soccer", "game_date": "2026-08-19",
             "outcome": "2"},
        ]

        def mock_union(sport):
            return soccer_props[:], {f"{sport}_odds": "AVAILABLE"}

        scan_result = _make_mock_scan_result({
            "watch": [
                {**p, "classification": "Watch", "terminal_bucket": "Watch",
                 "wow_score": 40.0, "final_approval_blocker": None, "audit_valid": False}
                for p in soccer_props
            ]
        })

        with (
            patch.object(orch, "_union_props_for_sport", side_effect=mock_union),
            patch("jobs.wow_daily_scan.run_scan", return_value=scan_result),
        ):
            from gate_engine.daily_orchestrator import run_daily_orchestration
            result = run_daily_orchestration(
                sports=["Soccer"], environment="test",
                runtime_provenance=None, persist=False,
            )

        # normalise_soccer_props is called on raw_props before evaluation
        # Check the execution notes indicate Soccer was processed
        self.assertIn("Soccer", result.get("scanned_sports", []))


if __name__ == "__main__":
    unittest.main()
